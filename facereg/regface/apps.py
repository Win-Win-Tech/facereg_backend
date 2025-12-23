from django.apps import AppConfig


class RegfaceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'regface'

    def ready(self):
        import sys
        import threading
        
        # Prevent running during migrations or management commands
        if 'runserver' in sys.argv or 'gunicorn' in sys.argv or 'uwsgi' in sys.argv:
            def load_faiss_index():
                try:
                    from .models import Employee
                    from .face_index import FaceIndexManager
                    
                    # Simple retry mechanism or just wait a bit?
                    # Usually threading is enough to delay past the "AppConfig.ready" check.
                    
                    try:
                        employees = Employee.objects.filter(face_encoding__isnull=False)
                        count = employees.count()
                        if count > 0:
                            FaceIndexManager.get_instance().rebuild(employees)
                        else:
                            print("FAISS: No employees found to index.")
                    except Exception as db_e:
                        print(f"FAISS: Database error during startup (skipping index build): {db_e}")
                        
                except Exception as e:
                    print(f"Error initializing FAISS: {e}")

            # Start in a background thread to avoid blocking startup and "AppRegistryNotReady" warnings
            thread = threading.Thread(target=load_faiss_index)
            thread.daemon = True
            thread.start()
