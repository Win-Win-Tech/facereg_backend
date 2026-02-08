from django.apps import AppConfig


class RegfaceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'regface'

    def ready(self):
        import sys
        import threading
        import logging
        logger = logging.getLogger(__name__)
        
        # Log sys.argv to help debug startup issues on different environments
        logger.info(f"RegfaceConfig.ready called. sys.argv: {sys.argv}")

        # Prevent running during migrations or management commands
        # We want to run for: runserver, gunicorn, uwsgi, or if no command (default)
        skip_commands = {'migrate', 'makemigrations', 'collectstatic', 'shell', 'test'}
        if len(sys.argv) > 1 and sys.argv[1] in skip_commands:
            logger.info(f"Skipping FAISS index build for command: {sys.argv[1]}")
            return
        
        def load_faiss_index():
            try:
                from .models import Employee
                from .face_index import FaceIndexManager
                
                logger.info("Starting FAISS index build...")
                try:
                    employees = Employee.objects.filter(face_encoding__isnull=False).exclude(face_encoding=b'')
                    count = employees.count()
                    if count > 0:
                        FaceIndexManager.get_instance().rebuild(employees)
                        logger.info(f"FAISS: Successfully indexed {count} employees.")
                    else:
                        logger.warning("FAISS: No employees found with face encodings to index.")
                except Exception as db_e:
                    logger.error(f"FAISS: Database error during startup: {db_e}")
                        
            except Exception as e:
                logger.error(f"Error initializing FAISS: {e}")

        # Start in a background thread to avoid blocking startup
        thread = threading.Thread(target=load_faiss_index)
        thread.daemon = True
        thread.start()
