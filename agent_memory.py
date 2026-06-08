import os
import sys
import psycopg2
from psycopg2.extras import RealDictCursor
from sentence_transformers import SentenceTransformer

class SupabaseAgentMemory:
    def __init__(self):
        print("Initializing Agent Memory connected to Supabase Production...")
        # Intentamos usar la de pooler que ya validamos que funciona con IPv4 en tu red
        db_uri = os.getenv("SUPABASE_POOLER_DB_URI") or os.getenv("SUPABASE_DB_URI")
        
        if not db_uri:
            print("❌ Error: No database URI found in environment variables.")
            sys.exit(1)
            
        try:
            self.conn = psycopg2.connect(db_uri)
            # Usamos RealDictCursor para que nos devuelva las filas como diccionarios de Python
            self.conn.cursor_factory = RealDictCursor
        except Exception as e:
            print(f"❌ Error connecting to Supabase from Memory module: {e}")
            raise
            
        print("Loading local embedding model (all-MiniLM-L6-v2) for queries...")
        self.encoder = SentenceTransformer('all-MiniLM-L6-v2')

    def get_injury_context(self, user_query: str, threshold: float = 0.3, limit: int = 3):
        """Queries Supabase using pgvector cosine distance (<=>)"""
        # Generamos el embedding de la consulta del usuario
        query_vector = self.encoder.encode(user_query).tolist()
        
        results = []
        # El operador <=> calcula la distancia de coseno. 
        # Como es distancia, "1 - distancia" nos da la similitud de coseno.
        query = """
        SELECT 
            log_date::text as date, 
            original_text as text, 
            (1 - (embedding <=> %s::vector)) as similarity
        FROM injury_logs
        WHERE (1 - (embedding <=> %s::vector)) >= %s
        ORDER BY similarity DESC
        LIMIT %s;
        """
        
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, (str(query_vector), str(query_vector), threshold, limit))
                rows = cur.fetchall()
                for row in rows:
                    results.append({
                        "date": row["date"][:10] if row["date"] else "N/A",
                        "text": row["text"],
                        "similarity": round(float(row["similarity"]), 3)
                    })
        except Exception as e:
            print(f"⚠️ Error during vector search in Supabase: {e}")
            
        return results

    def get_latest_metrics(self):
        """Returns the most recent physiological training metrics from production DB"""
        query = """
        SELECT 
            activity_name, activity_type, distance_meters, 
            moving_time_seconds, elevation_gain_meters, rpe, 
            icu_load, fitness_ctl, fatigue_atl, form_tsb
        FROM training_metrics
        ORDER BY activity_date DESC
        LIMIT 1;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(query)
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            print(f"⚠️ Error fetching latest metrics from Supabase: {e}")
            return None

    def close(self):
        self.conn.close()