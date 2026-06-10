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

    _SORT_COLUMNS = {
        "date": "activity_date",
        "distance": "distance_meters",
        "duration": "moving_time_seconds",
        "elevation": "elevation_gain_meters",
        "load": "icu_load",
    }

    def get_recent_activities(self, limit: int = 10, sort_by: str = "date"):
        """Returns the N most recent training activities with full metrics"""
        sort_column = self._SORT_COLUMNS.get(sort_by, "activity_date")
        query = f"""
        SELECT
            activity_date::text, activity_name, activity_type,
            distance_meters, moving_time_seconds, elevation_gain_meters,
            rpe, icu_load, fitness_ctl, fatigue_atl, form_tsb
        FROM training_metrics
        ORDER BY {sort_column} DESC NULLS LAST
        LIMIT %s;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, (limit,))
                rows = cur.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"⚠️ Error fetching recent activities from Supabase: {e}")
            return []

    def get_activities_in_range(self, start_date: str, end_date: str, activity_type: str | None = None, limit: int = 50, sort_by: str = "date"):
        """Returns activities within a date range, optionally filtered by sport type"""
        sort_column = self._SORT_COLUMNS.get(sort_by, "activity_date")
        base_query = f"""
        SELECT
            activity_date::text, activity_name, activity_type,
            distance_meters, moving_time_seconds, elevation_gain_meters,
            rpe, icu_load, fitness_ctl, fatigue_atl, form_tsb
        FROM training_metrics
        WHERE activity_date BETWEEN %s AND %s
        """
        params: list = [start_date, end_date]
        if activity_type:
            base_query += " AND LOWER(activity_type) = LOWER(%s)"
            params.append(activity_type)
        base_query += f" ORDER BY {sort_column} DESC NULLS LAST LIMIT %s;"
        params.append(limit)
        try:
            with self.conn.cursor() as cur:
                cur.execute(base_query, params)
                rows = cur.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"⚠️ Error fetching activities in range from Supabase: {e}")
            return []

    def get_personal_bests(self, activity_type: str | None = None):
        """Returns personal bests and lifetime stats, optionally filtered by sport type"""
        base_query = """
        SELECT
            MAX(distance_meters)        AS max_distance_m,
            MAX(moving_time_seconds)    AS max_duration_s,
            MAX(elevation_gain_meters)  AS max_elevation_m,
            MAX(fitness_ctl)            AS max_ctl,
            MAX(icu_load)               AS max_load,
            MIN(form_tsb)               AS lowest_tsb,
            COUNT(*)                    AS total_activities
        FROM training_metrics
        """
        params: list = []
        if activity_type:
            base_query += " WHERE LOWER(activity_type) = LOWER(%s)"
            params.append(activity_type)
        base_query += ";"
        try:
            with self.conn.cursor() as cur:
                cur.execute(base_query, params)
                row = cur.fetchone()
                return dict(row) if row else {}
        except Exception as e:
            print(f"⚠️ Error fetching personal bests from Supabase: {e}")
            return {}

    def get_volume_summary(self, group_by: str, start_date: str, end_date: str, activity_type: str | None = None):
        """Returns aggregated weekly or monthly volume stats from the DB"""
        if group_by not in ("week", "month"):
            return []
        query = f"""
        SELECT
            DATE_TRUNC('{group_by}', activity_date)::date::text  AS period,
            COUNT(*)                                              AS num_activities,
            ROUND(SUM(distance_meters) / 1000.0, 1)              AS total_km,
            ROUND(SUM(moving_time_seconds) / 3600.0, 2)          AS total_hours,
            ROUND(SUM(elevation_gain_meters))                     AS total_elevation_m,
            ROUND(AVG(fitness_ctl), 1)                            AS avg_ctl,
            ROUND(AVG(fatigue_atl), 1)                            AS avg_atl,
            ROUND(AVG(form_tsb), 1)                               AS avg_tsb
        FROM training_metrics
        WHERE activity_date BETWEEN %s AND %s
        {"AND LOWER(activity_type) = LOWER(%s)" if activity_type else ""}
        GROUP BY 1
        ORDER BY 1 DESC;
        """
        params: list = [start_date, end_date]
        if activity_type:
            params.append(activity_type)
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"⚠️ Error fetching volume summary from Supabase: {e}")
            return []

    def get_ctl_trend(self, start_date: str, end_date: str):
        """Returns weekly CTL/ATL/TSB evolution for fitness trend analysis"""
        query = """
        SELECT
            DATE_TRUNC('week', activity_date)::date::text AS week,
            ROUND(AVG(fitness_ctl), 1)  AS avg_ctl,
            ROUND(MAX(fitness_ctl), 1)  AS peak_ctl,
            ROUND(AVG(fatigue_atl), 1)  AS avg_atl,
            ROUND(AVG(form_tsb), 1)     AS avg_tsb,
            MIN(form_tsb)               AS lowest_tsb
        FROM training_metrics
        WHERE activity_date BETWEEN %s AND %s
            AND fitness_ctl IS NOT NULL
        GROUP BY 1
        ORDER BY 1 ASC;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, (start_date, end_date))
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            print(f"⚠️ Error fetching CTL trend: {e}")
            return []

    def get_sport_breakdown(self, start_date: str, end_date: str):
        """Returns km, hours and activity count per sport type"""
        query = """
        SELECT
            activity_type,
            COUNT(*)                                    AS num_activities,
            ROUND(SUM(distance_meters) / 1000.0, 1)    AS total_km,
            ROUND(SUM(moving_time_seconds) / 3600.0, 2) AS total_hours,
            ROUND(SUM(elevation_gain_meters))           AS total_elevation_m
        FROM training_metrics
        WHERE activity_date BETWEEN %s AND %s
        GROUP BY activity_type
        ORDER BY total_hours DESC;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, (start_date, end_date))
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            print(f"⚠️ Error fetching sport breakdown: {e}")
            return []

    def get_rest_days(self, start_date: str, end_date: str):
        """Returns total days in range, active days and rest days"""
        query = """
        WITH date_series AS (
            SELECT generate_series(%s::date, %s::date, '1 day'::interval)::date AS day
        ),
        active_days AS (
            SELECT DISTINCT activity_date::date AS day
            FROM training_metrics
            WHERE activity_date BETWEEN %s AND %s
        )
        SELECT
            COUNT(*)                                            AS total_days,
            COUNT(active_days.day)                              AS active_days,
            COUNT(*) - COUNT(active_days.day)                   AS rest_days,
            ROUND(100.0 * COUNT(active_days.day) / COUNT(*), 1) AS active_pct
        FROM date_series
        LEFT JOIN active_days ON date_series.day = active_days.day;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, (start_date, end_date, start_date, end_date))
                row = cur.fetchone()
                return dict(row) if row else {}
        except Exception as e:
            print(f"⚠️ Error fetching rest days: {e}")
            return {}

    def compare_periods(self, start_a: str, end_a: str, start_b: str, end_b: str, activity_type: str | None = None):
        """Returns aggregated stats for two periods side by side for comparison"""
        period_query = """
        SELECT
            COUNT(*)                                        AS num_activities,
            ROUND(SUM(distance_meters) / 1000.0, 1)        AS total_km,
            ROUND(SUM(moving_time_seconds) / 3600.0, 2)    AS total_hours,
            ROUND(SUM(elevation_gain_meters))               AS total_elevation_m,
            ROUND(AVG(fitness_ctl), 1)                      AS avg_ctl,
            ROUND(AVG(fatigue_atl), 1)                      AS avg_atl,
            ROUND(AVG(form_tsb), 1)                         AS avg_tsb,
            ROUND(AVG(rpe), 1)                              AS avg_rpe
        FROM training_metrics
        WHERE activity_date BETWEEN %s AND %s
        {sport_filter};
        """
        sport_filter = "AND LOWER(activity_type) = LOWER(%s)" if activity_type else ""
        q = period_query.format(sport_filter=sport_filter)

        def fetch(start, end):
            params = [start, end]
            if activity_type:
                params.append(activity_type)
            with self.conn.cursor() as cur:
                cur.execute(q, params)
                row = cur.fetchone()
                return dict(row) if row else {}

        try:
            return {
                f"period_a ({start_a} to {end_a})": fetch(start_a, end_a),
                f"period_b ({start_b} to {end_b})": fetch(start_b, end_b),
            }
        except Exception as e:
            print(f"⚠️ Error comparing periods: {e}")
            return {}

    def get_streak(self):
        """Returns current training streak and longest historical streak in days"""
        query = """
        WITH active_days AS (
            SELECT DISTINCT activity_date::date AS day
            FROM training_metrics
            ORDER BY day
        ),
        groups AS (
            SELECT day,
                   day - (ROW_NUMBER() OVER (ORDER BY day) * INTERVAL '1 day')::date AS grp
            FROM active_days
        ),
        streaks AS (
            SELECT MIN(day) AS start_date, MAX(day) AS end_date,
                   COUNT(*) AS streak_days
            FROM groups
            GROUP BY grp
        )
        SELECT
            streak_days                     AS longest_streak_days,
            start_date::text                AS longest_streak_start,
            end_date::text                  AS longest_streak_end,
            (SELECT streak_days FROM streaks ORDER BY end_date DESC LIMIT 1)
                                            AS current_streak_days,
            (SELECT end_date::text FROM streaks ORDER BY end_date DESC LIMIT 1)
                                            AS current_streak_last_day
        FROM streaks
        ORDER BY streak_days DESC
        LIMIT 1;
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(query)
                row = cur.fetchone()
                return dict(row) if row else {}
        except Exception as e:
            print(f"⚠️ Error fetching streak: {e}")
            return {}

    def get_activity_ids_for_date(self, target_date: str) -> list:
        """Returns activity_id and name for all activities on a given date"""
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT activity_id, activity_name, activity_type
                    FROM training_metrics
                    WHERE activity_date::date = %s
                    ORDER BY activity_date;
                    """,
                    (target_date,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"⚠️ Error fetching activity ids for {target_date}: {e}")
            return []

    def update_rpe_local(self, activity_id: str, rpe: int) -> bool:
        """Updates RPE in the local training_metrics table"""
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE training_metrics SET rpe = %s WHERE activity_id = %s;",
                    (rpe, activity_id),
                )
                self.conn.commit()
                return True
        except Exception as e:
            print(f"⚠️ Error updating local RPE: {e}")
            self.conn.rollback()
            return False

    def add_training_note(self, note: str, note_date: str):
        """Inserts a new subjective training note into injury_logs and computes its embedding"""
        try:
            vector = self.encoder.encode(note).tolist()
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO injury_logs (log_date, log_type, original_text, embedding)
                    VALUES (%s, 'chat_note', %s, %s::vector)
                    RETURNING id;
                    """,
                    (note_date, note, str(vector)),
                )
                new_id = cur.fetchone()["id"]
                self.conn.commit()
            return {"status": "saved", "id": new_id, "date": note_date}
        except Exception as e:
            print(f"⚠️ Error saving training note: {e}")
            self.conn.rollback()
            return {"status": "error", "detail": str(e)}

    def close(self):
        self.conn.close()