import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
try:
    import pythoncom
    import win32com.client
    OUTLOOK_AVAILABLE = True
except ImportError:
    OUTLOOK_AVAILABLE = False

from services.cams_ingester import ingest_cams_pdf
from services.fifo_engine import invalidate_remaining_units_cache
from models.audit import ImportBatch

def scan_and_sync_cams_from_outlook(db: Session, days_back: int = 7) -> dict:
    """
    Connects to the local Outlook desktop application, scans the Inbox for CAMS statements,
    downloads them, and ingests them into the database.
    
    Returns a summary dict with success/error details.
    """
    if not OUTLOOK_AVAILABLE:
        return {
            "success": False,
            "error": "Outlook synchronization is only supported when running natively on Windows. The win32com library is not available in this environment (e.g. inside Docker/Linux)."
        }
        
    # Initialize COM library for the current thread (critical for web server worker threads)
    pythoncom.CoInitialize()
    
    success_files = []
    skipped_files = []
    failed_files = []
    
    try:
        # Get existing imported file names to avoid double parsing
        existing_batches = db.query(ImportBatch.file_name).filter(
            ImportBatch.asset_class == "Mutual Funds"
        ).all()
        existing_filenames = {b[0] for b in existing_batches if b[0]}
        
        # Connect to local Outlook via COM
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        
        # Get default Inbox folder (olFolderInbox = 6)
        inbox = namespace.GetDefaultFolder(6)
        messages = inbox.Items
        
        # Sort messages by received time descending
        messages.Sort("[ReceivedTime]", True)
        
        # Calculate cutoff time (last 7 days)
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        
        # Create a persistent downloads directory under DATA/
        download_dir = os.path.abspath("./DATA/cams_outlook_downloads")
        os.makedirs(download_dir, exist_ok=True)
        
        seen_prefixes = set()
        
        # Process emails (newest first)
        for msg in messages:
            try:
                # Convert ReceivedTime to UTC for comparison
                rec_time = msg.ReceivedTime
                rec_time_utc = rec_time.astimezone(timezone.utc)
                
                if rec_time_utc < cutoff_date:
                    break
                
                # Check subject strictly
                subject = msg.Subject or ""
                if "Consolidated Account Statement - CAMS Mailback Request" in subject:
                    for attachment in msg.Attachments:
                        filename = attachment.FileName
                        if not filename:
                            continue
                        
                        # Check for PDF extension
                        if filename.lower().endswith(".pdf"):
                            # Extract account / statement prefix
                            parts = filename.split('_')
                            if parts:
                                prefix = parts[0].upper()
                                if prefix.startswith("AA") and len(prefix) == 10:
                                    if prefix in seen_prefixes:
                                        continue
                                    seen_prefixes.add(prefix)
                                    
                            # Skip if already ingested
                            if filename in existing_filenames:
                                if filename not in skipped_files:
                                    skipped_files.append(filename)
                                continue
                            
                            # Save the attachment temporarily
                            temp_dir = tempfile.mkdtemp()

                            temp_file_path = os.path.join(temp_dir, filename)
                            
                            try:
                                attachment.SaveAsFile(temp_file_path)
                                
                                # Ingest file into database
                                result = ingest_cams_pdf(
                                    file_path=temp_file_path,
                                    password=None, # Ingester will automatically guess the password
                                    db=db,
                                    original_filename=filename
                                )
                                
                                # Copy the file to the persistent download directory for record keeping
                                persistent_path = os.path.join(download_dir, filename)
                                shutil.copy(temp_file_path, persistent_path)
                                
                                success_files.append({
                                    "filename": filename,
                                    "transactions": result.get("transactions_imported", 0),
                                    "person": result.get("person_name", "Unknown")
                                })
                                
                                # Add to temporary cache so we don't process same file from another email thread
                                existing_filenames.add(filename)
                                
                            except Exception as e:
                                failed_files.append({
                                    "filename": filename,
                                    "error": str(e)
                                })
                            finally:
                                # Clean up temp folder
                                try:
                                    shutil.rmtree(temp_dir)
                                except:
                                    pass
            except Exception:
                # Skip any items that don't have ReceivedTime (like meeting invites or system reports)
                continue

                            
        # If new files were successfully ingested, invalidate the FIFO calculation cache
        if success_files:
            invalidate_remaining_units_cache(db)
            
        summary = {
            "success": True,
            "success_count": len(success_files),
            "skipped_count": len(skipped_files),
            "failed_count": len(failed_files),
            "success_files": success_files,
            "skipped_files": skipped_files,
            "failed_files": failed_files
        }
        return summary
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Error scanning Outlook: {str(e)}"
        }
    finally:
        # Always clean up the COM library unitialization on the thread
        pythoncom.CoUninitialize()
