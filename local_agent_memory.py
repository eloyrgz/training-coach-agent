import json
import os

class LocalAgentMemory:
    def __init__(self, mock_db_path: str = "local_db_mock.json"):
        self.mock_db_path = mock_db_path
        print("FORCED OFFLINE MODE: Simulating Vector Space via Keyword Matching...")
        
        # Load local mock database
        with open(mock_db_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)

    def get_injury_context(self, user_query: str, threshold: float = 0.1, limit: int = 3):
        """Simulates Supabase vector search offline by looking for semantic word overlaps"""
        query_words = set(user_query.lower().replace(",", "").replace(".", "").split())
        results = []

        # Simple dictionary mapping of related concepts to simulate 'embeddings' behavior
        synonyms = {
            "foot": ["foot", "neuroma", "forefoot", "downhills", "toe", "fascia", "ankle"],
            "pain": ["pain", "inflammation", "swollen", "sensitive", "sore", "injury", "ache"],
            "injury": ["pain", "inflammation", "neuroma", "swollen", "sensitive"]
        }

        # Expand query words with basic synonyms to mimic vector embeddings meaning
        expanded_query = set(list(query_words))
        for word in query_words:
            if word in synonyms:
                expanded_query.update(synonyms[word])

        for log in self.data["injury_logs"]:
            log_text_lower = log["original_text"].lower()
            log_words = set(log_text_lower.replace(",", "").replace(".", "").split())
            
            # Calculate match score based on intersection
            matches = expanded_query.intersection(log_words)
            score = len(matches) / max(len(query_words), 1)

            if score > threshold:
                results.append({
                    "date": log["log_date"][:10],
                    "text": log["original_text"],
                    "similarity": round(float(score), 3)
                })

        # Sort by match score descending
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
    print("\n--- SIMULATED VECTOR MATCHES FOUND (OFFLINE) ---")
    if hits:
        for hit in hits:
            print(f"[{hit['date']}] (Simulated Match Score: {hit['similarity']}) -> {hit['text']}")
    else:
        print("No matches found.")
        
    print("\n--- LATEST PHYSIOLOGICAL METRICS ---")
    print(memory.get_latest_metrics())