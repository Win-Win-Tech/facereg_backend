import faiss
import numpy as np
import logging

logger = logging.getLogger(__name__)

class FaceIndexManager:
    _instance = None
    
    def __init__(self):
        # Dimension 128 is standard for face_recognition
        self.dimension = 128
        self.index = faiss.IndexFlatL2(self.dimension)
        self.employee_map = {}  # Maps FAISS ID (0, 1, 2...) -> Employee DB ID
        self.next_id = 0

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def add_employee(self, employee_id, encoding_bytes):
        """Adds a single employee to the index."""
        if not encoding_bytes:
            return
            
        try:
            # Convert bytes back to numpy float32 array
            vector = np.frombuffer(encoding_bytes, dtype=np.float64).astype('float32')
            
            # FAISS expects a matrix (list of vectors), so we wrap it in [ ]
            self.index.add(np.array([vector]))
            
            # Map the internal FAISS ID to the Database ID
            self.employee_map[self.next_id] = employee_id
            self.next_id += 1
        except Exception as e:
            logger.error(f"Error adding employee {employee_id} to FAISS index: {e}")

    def search(self, input_encoding, threshold=0.45):
        """Searches for the closest match."""
        if self.index.ntotal == 0:
            return None, float('inf')

        try:
            # Convert input to float32
            query_vector = input_encoding.astype('float32').reshape(1, -1)
            
            # Search for the 1 closest match
            distances, indices = self.index.search(query_vector, 1)
            
            best_distance = distances[0][0]
            best_index = indices[0][0]

            if best_distance <= threshold and best_index != -1:
                employee_id = self.employee_map.get(best_index)
                logger.info(f"FAISS Match Found: Employee ID {employee_id}, Distance {best_distance:.4f}")
                return employee_id, best_distance
                
            logger.info(f"FAISS No Match: Best Distance {best_distance:.4f} (Threshold {threshold})")
            return None, best_distance
        except Exception as e:
            logger.error(f"Error searching FAISS index: {e}")
            return None, float('inf')

    def rebuild(self, employees):
        """Called on startup to load all employees."""
        self.index.reset()
        self.employee_map = {}
        self.next_id = 0
        
        # Prepare batch data
        vectors = []
        ids = []
        
        for emp in employees:
            if emp.face_encoding:
                try:
                    vec = np.frombuffer(emp.face_encoding, dtype=np.float64).astype('float32')
                    vectors.append(vec)
                    ids.append(emp.id)
                except Exception as e:
                    logger.error(f"Error decoding face encoding for employee {emp.id}: {e}")
        
        if vectors:
            # Add all at once for speed
            self.index.add(np.array(vectors))
            # Create the map
            for i, emp_id in enumerate(ids):
                self.employee_map[i] = emp_id
                
        logger.info(f"FAISS Index rebuilt with {len(vectors)} faces.")
        self.next_id = len(vectors)
