import psycopg2
from sentence_transformers import Transformer, SentenceTransformer

class AgentMemory:
    def __init__(self, db_uri: str):
        self.db_conn = psycopg2.connect(db_uri)
        self.encoder = SentenceTransformer('all-MiniLM-L6-v2')

    def get_injury_context(self, user_query: str, threshold: float = 0.4, limit: int = 3):
        """Converts user input into a vector and queries Supabase for historical match."""
        query_vector = self.encoder.encode(user_query).tolist()
        
        with self.db_conn.cursor() as cur:
            cur.execute(
                "SELECT id, log_date, original_text, similarity FROM search_injury_logs(%s, %s, %s);",
                (str(query_vector), threshold, limit)
            )
            results = cur.fetchall()
            
            context = []
            for row in results:
                row_id, log_date, text, similarity = row
                context.append({
                    "date": log_date.strftime("%Y-%m-%d"),
                    "text": text,
                    "similarity": round(similarity, 3)
                })
            return context

    def close(self):
        self.db_conn.close()