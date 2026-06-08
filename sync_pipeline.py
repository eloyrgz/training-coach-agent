import os
import sys
import socket
import requests
import psycopg2
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from urllib.parse import urlparse

# Load environment variables from the .env file
load_dotenv(override=True)

class TrainingDataPipeline:
    def __init__(self, athlete_id: str, icu_api_key: str, db_uri: str, fallback_db_uri: str | None = None):
        self.athlete_id = athlete_id
        self.icu_auth = ('API_KEY', icu_api_key)
        self.icu_base_url = f"https://intervals.icu/api/v1/athlete/{athlete_id}"
        self.db_conn = self._connect_with_fallback(db_uri, fallback_db_uri)
        
        print("Loading local embedding model (all-MiniLM-L6-v2)...")
        self.encoder = SentenceTransformer('all-MiniLM-L6-v2')

    def _connect_with_fallback(self, primary_uri: str, fallback_uri: str | None):
        try:
            return psycopg2.connect(primary_uri)
        except psycopg2.OperationalError as primary_error:
            host = self._extract_host(primary_uri)
            self._print_dns_hint(host, primary_error)

            if fallback_uri:
                fallback_host = self._extract_host(fallback_uri)
                if fallback_host and host and fallback_host == host:
                    print(
                        "Hint: SUPABASE_POOLER_DB_URI is using the same host as SUPABASE_DB_URI. "
                        "Use the pooler host from Supabase -> Database -> Connection pooling."
                    )
                print("Primary DB connection failed. Retrying with SUPABASE_POOLER_DB_URI...")
                return psycopg2.connect(fallback_uri)

            raise

    @staticmethod
    def _extract_host(db_uri: str) -> str | None:
        try:
            return urlparse(db_uri).hostname
        except Exception:
            return None

    @staticmethod
    def _print_dns_hint(host: str | None, error: Exception):
        message = str(error)
        if "could not translate host name" not in message:
            return

        if not host:
            print("Hint: Verify SUPABASE_DB_URI is a valid postgres URI.")
            return

        try:
            infos = socket.getaddrinfo(host, None)
            has_ipv4 = any(info[0] == socket.AF_INET for info in infos)
            has_ipv6 = any(info[0] == socket.AF_INET6 for info in infos)

            if has_ipv6 and not has_ipv4:
                print(
                    "Hint: This DB host resolves to IPv6 only from your network. "
                    "Use a Supabase pooler URI (IPv4) in SUPABASE_POOLER_DB_URI."
                )
            else:
                print("Hint: Check DNS, firewall, or VPN/proxy restrictions for this host.")
        except socket.gaierror:
            print("Hint: Hostname cannot be resolved in this network. Check DNS/VPN/firewall policies.")

    def sync_activities(self, days_back: int = 90):
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days_back)
        
        print(f"Fetching activities from Intervals.icu since {start_date}...")
        url = f"{self.icu_base_url}/activities"
        params = {"oldest": start_date.isoformat(), "newest": end_date.isoformat()}
        
        response = requests.get(url, auth=self.icu_auth, params=params)
        response.raise_for_status()
        activities = response.json()
        
        print(f"DEBUG: Number of activities received from Intervals API: {len(activities)}")
        
        with self.db_conn.cursor() as cur:
            for act in activities:
                activity_id = str(act.get('id'))
                act_date = act.get('start_date_local')
                name = act.get('name')
                act_type = act.get('type')
                distance = act.get('distance', 0.0)
                duration = act.get('moving_time', 0)
                elevation = act.get('total_elevation_gain', 0.0)
                rpe = act.get('rpe')
                load = act.get('icu_training_load')
                ctl = act.get('ctl')
                atl = act.get('atl')
                tsb = act.get('tsb')
                
                # 1. Upsert quantitative metrics
                upsert_query = """
                INSERT INTO training_metrics 
                (activity_id, activity_date, activity_name, activity_type, distance_meters, moving_time_seconds, elevation_gain_meters, rpe, icu_load, fitness_ctl, fatigue_atl, form_tsb)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (activity_id) DO UPDATE SET
                    activity_name = EXCLUDED.activity_name,
                    rpe = COALESCE(EXCLUDED.rpe, training_metrics.rpe),
                    icu_load = COALESCE(EXCLUDED.icu_load, training_metrics.icu_load),
                    fitness_ctl = COALESCE(EXCLUDED.fitness_ctl, training_metrics.fitness_ctl),
                    fatigue_atl = COALESCE(EXCLUDED.fatigue_atl, training_metrics.fatigue_atl),
                    form_tsb = COALESCE(EXCLUDED.form_tsb, training_metrics.form_tsb);
                """
                cur.execute(upsert_query, (activity_id, act_date, name, act_type, distance, duration, elevation, rpe, load, ctl, atl, tsb))
                
                # 2. Extract subjective logs for context
                notes = act.get('developer_text') or act.get('description')
                if notes:
                    cur.execute("SELECT id FROM injury_logs WHERE activity_id = %s", (activity_id,))
                    if not cur.fetchone():
                        cur.execute(
                            "INSERT INTO injury_logs (activity_id, log_date, log_type, original_text) VALUES (%s, %s, 'workout_note', %s);",
                            (activity_id, act_date, notes)
                        )
            
            self.db_conn.commit()
        
        print("Metrics synced successfully. Processing vector embeddings...")
        self._generate_pending_embeddings()

    def _generate_pending_embeddings(self):
        with self.db_conn.cursor() as cur:
            cur.execute("SELECT id, original_text FROM injury_logs WHERE embedding IS NULL;")
            pending_rows = cur.fetchall()
            
            if not pending_rows:
                print("No pending embeddings to compute.")
                return
                
            print(f"Computing embeddings for {len(pending_rows)} records...")
            for row_id, text in pending_rows:
                vector = self.encoder.encode(text).tolist()
                cur.execute(
                    "UPDATE injury_logs SET embedding = %s WHERE id = %s;",
                    (str(vector), row_id)
                )
            self.db_conn.commit()
            print("All vector embeddings updated in Supabase.")

    def close(self):
        self.db_conn.close()

# --- ENTRY POINT: CONFIGURATION LOADED FROM ENVIRONMENT ---
if __name__ == "__main__":
    print("Starting pipeline using environment configuration...")
    
    # Read variables loaded by load_dotenv()
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
    icu_api_key = os.getenv("INTERVALS_API_KEY")
    supabase_uri = os.getenv("SUPABASE_DB_URI")
    supabase_pooler_uri = os.getenv("SUPABASE_POOLER_DB_URI")
    
    # 🔍 LÍNEAS DE DIAGNÓSTICO TEMPORALES:
    if icu_api_key:
        print(f"DEBUG - Longitud de la API Key: {len(icu_api_key)}")
        print(f"DEBUG - Empieza por: '{icu_api_key[:4]}...' y termina por: '...{icu_api_key[-4:]}'")

    # Assert configuration is complete
    if not all([athlete_id, icu_api_key, supabase_uri]):
        print("❌ Error: Missing configuration variables in your .env file.")
        print("Please check INTERVALS_ATHLETE_ID, INTERVALS_API_KEY, and SUPABASE_DB_URI.")
        sys.exit(1)
        
    try:
        pipeline = TrainingDataPipeline(
            athlete_id=athlete_id, 
            icu_api_key=icu_api_key, 
            db_uri=supabase_uri,
            fallback_db_uri=supabase_pooler_uri
        )
        
        # Pull data from the last 90 days
        pipeline.sync_activities(days_back=90)
        pipeline.close()
        print("🏁 Execution finished successfully.")
        
    except Exception as e:
        print(f"❌ Execution failed with error: {e}")