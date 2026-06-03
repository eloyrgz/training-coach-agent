import json
import os
import numpy as np
from sentence_transformers import SentenceTransformer

class LocalAgentMemory:
    def __init__(self, mock_db_path: str = "local_db_mock.json"):
        self.mock_db_path = mock_db_path
        print("Loading local embedding model (all-MiniLM-L6-v2) in Mock Mode...")
        self.encoder = SentenceTransformer('all-MiniLM-L6-v2')
        
        # Load local mock database
        with open(mock_db_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)
            
        # Pre-compute embeddings for our mock injury logs
        print("Pre-computing vectors for mock database...")
        self.logs_with_vectors = []
        for log in self.data["injury_logs"]:
            vector = self.encoder.encode(log["original_text"])
            self.logs_with_vectors.append({
                "date": log["log_date"][:10],
                "text": log["original_text"],
                "vector": vector
            })

    def get_injury_context(self, user_query: str, threshold: float = 0.3, limit: int = 3):
        """Simulates Supabase vector search locally using Cosine Similarity"""
        query_vector = self.encoder.encode(user_query)
        results = []

        for log in self.logs_with_vectors:
            # Mathematical cosine similarity calculation: (A . B) / (||A|| * ||B||)
            dot_product = np.dot(query_vector, log["vector"])
            norm_q = np.linalg.norm(query_vector)
            norm_l = np.linalg.norm(log["vector"])
            similarity = dot_product / (norm_q * norm_l)

            if similarity >= threshold:
                results.append({
                    "date": log["date"],
                    "text": log["text"],
                    "similarity": round(float(similarity), 3)
                })

        # Sort by similarity descending
        results = sorted(results, key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    def get_latest_metrics(self):
        """Returns the most recent physiological training metrics from mock data"""
        sorted_metrics = sorted(self.data["training_metrics"], key=lambda x: x["activity_date"], reverse=True)
        return sorted_metrics[0] if sorted_metrics else None

# --- QUICK LOCAL VERIFICATION ---
if __name__ == "__main__":
    memory = LocalAgentMemory()
    
    # Test query that mimics user concerns about foot issues
    test_query = "foot pain and inflammation issues during training"
    print(f"\nSearching local memory for: '{test_query}'...")
    
    hits = memory.get_injury_context(test_query)
    print("\n--- VECTOR MATCHES FOUND IN PORTABLE MEMORY ---")
    for hit in hits:
        print(f"[{hit['date']}] (Match: {hit['similarity']}) -> {hit['text']}")
        
    print("\n--- LATEST PHYSIOLOGICAL METRICS ---")
    print(memory.get_latest_metrics())