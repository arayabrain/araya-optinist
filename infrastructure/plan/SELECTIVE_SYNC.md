Selective File Sync Implementation Plan                                                                                        
                                                                                                                                
 Overview                                                                                                                       
                                                                                                                                
 Implement selective file syncing to reduce bandwidth and storage by 80-95% by syncing only essential files for published       
 experiments while downloading large user-specific files lazily.                                                                
                                                                                                                                
 User Requirements (Confirmed)                                                                                                  
                                                                                                                                
 1. Published experiments (shared/public): Sync only files necessary for dataview every 5 minutes between instances             
   - Small files: .yaml, .json, cell_roi.json, .plot-meta.json                                                                  
   - Skip: .pkl (10MB-1GB), .nwb (100MB-5GB)                                                                                    
 2. User's own experiments: Download .pkl and .nwb files when user logs in (slower is OK)                                       
 3. NWB files confirmed: NOT needed for dataview - only for export/archival                                                     
                                                                                                                                
 Expected Impact                                                                                                                
                                                                                                                                
 - Published sync: 10-50MB instead of 500MB-5GB (95% reduction)                                                                 
 - Bandwidth savings: 80-95% across all operations                                                                              
 - User login: Remains fast (<2 seconds), large files download in background                                                    
 - Total daily bandwidth: Reduced from ~100GB to ~5GB                                                                           
                                                                                                                                
 Implementation Phases                                                                                                          
                                                                                                                                
 Phase 1: Core File Filtering                                                                                                   
                                                                                                                                
 1.1 Create File Filter Utility                                                                                                 
                                                                                                                                
 NEW FILE: /studio/app/common/core/storage/file_filter.py                                                                       
 - Implements FileSyncFilter class with pattern matching                                                                        
 - Default patterns: Essential (*.yaml, .json), Large (.pkl, *.nwb, *.tif)                                                      
 - Configurable via environment variables                                                                                       
 - Returns (should_sync, reason) for each file                                                                                  
                                                                                                                                
 1.2 Modify S3StorageController                                                                                                 
                                                                                                                                
 FILE: /studio/app/common/core/storage/s3_storage_controller.py                                                                 
 - Location: Lines 504-594 (download_experiment method)                                                                         
 - Changes:                                                                                                                     
   - Add sync_mode parameter: "all" (default), "essential_only", "large_only"                                                   
   - Import FileSyncFilter                                                                                                      
   - Add filtering logic in download loop (around line 551)                                                                     
   - Track metrics: files skipped, bytes saved                                                                                  
   - Log sync statistics                                                                                                        
                                                                                                                                
 1.3 Update PublishedExperimentSyncJob                                                                                          
                                                                                                                                
 FILE: /studio/app/common/core/background/sync_job.py                                                                           
 - Location: Line 220 (_sync_experiment method)                                                                                 
 - Changes:                                                                                                                     
   - Check SELECTIVE_SYNC_ENABLED environment variable                                                                          
   - Pass sync_mode="essential_only" when enabled                                                                               
   - Default to sync_mode="all" for backwards compatibility                                                                     
                                                                                                                                
 Phase 2: User-Specific Downloads                                                                                               
                                                                                                                                
 2.1 Create User File Downloader                                                                                                
                                                                                                                                
 NEW FILE: /studio/app/common/core/storage/user_file_downloader.py                                                              
 - Implements UserLargeFileDownloader class                                                                                     
 - Method: download_user_large_files(user_id, bucket_name, db)                                                                  
 - Gets user's accessible workspaces via WorkspaceService                                                                       
 - Downloads with sync_mode="large_only"                                                                                        
 - Runs asynchronously in background (non-blocking)                                                                             
 - Publishes CloudWatch metrics                                                                                                 
                                                                                                                                
 2.2 Integrate with Login Flow                                                                                                  
                                                                                                                                
 FILE: /studio/app/common/routers/auth.py                                                                                       
 - Location: Lines 20-70 (login function)                                                                                       
 - Changes:                                                                                                                     
   - Add BackgroundTasks parameter to function signature                                                                        
   - After metadata download (line 39), add background task:                                                                    
   background_tasks.add_task(                                                                                                   
     UserLargeFileDownloader.download_user_large_files,                                                                         
     user_id=user.id,                                                                                                           
     remote_bucket_name=remote_bucket_name,                                                                                     
     db=db                                                                                                                      
 )                                                                                                                              
   - Check SYNC_LARGE_FILES_ON_LOGIN environment variable                                                                       
                                                                                                                                
 Phase 3: Lazy Loading Fallback                                                                                                 
                                                                                                                                
 3.1 Add S3 Fallback to File Readers                                                                                            
                                                                                                                                
 FILE: /studio/app/common/core/utils/file_reader.py                                                                             
 - Changes:                                                                                                                     
   - Add helper: _download_from_s3_if_missing(filepath) (async)                                                                 
   - Modify JsonReader.read() to be async and call helper                                                                       
   - Modify Reader.read() to be async and call helper                                                                           
   - Parse workspace_id/experiment_id from filepath                                                                             
   - Download single file from S3 if missing locally                                                                            
   - Publish CloudWatch metric for lazy downloads                                                                               
                                                                                                                                
 3.2 Update Route Handlers                                                                                                      
                                                                                                                                
 FILE: /studio/app/common/routers/outputs.py                                                                                    
 - Locations: Lines 97, 115, 128, 139                                                                                           
 - Changes: Add await to reader calls (now async)                                                                               
   - get_timedata() - line 97                                                                                                   
   - get_alltimedata() - line 115                                                                                               
   - get_file() - line 128                                                                                                      
   - get_image() - line 139                                                                                                     
                                                                                                                                
 Phase 4: Configuration & Monitoring                                                                                            
                                                                                                                                
 4.1 Environment Configuration                                                                                                  
                                                                                                                                
 FILE: /studio/config/.env.example                                                                                              
 - Add new variables:                                                                                                           
 SELECTIVE_SYNC_ENABLED=false                                                                                                   
 SYNC_LARGE_FILES_ON_LOGIN=false                                                                                                
 LAZY_DOWNLOAD_ENABLED=false                                                                                                    
 LARGE_FILE_MIN_SIZE_MB=10                                                                                                      
 ESSENTIAL_FILE_PATTERNS=*.yaml,*.json,cell_roi.json,.plot-meta.json                                                            
 LARGE_FILE_PATTERNS=*.pkl,*.nwb,*.tif                                                                                          
                                                                                                                                
 4.2 CloudWatch Metrics                                                                                                         
                                                                                                                                
 - Published sync: FilesSkippedDuringSync, BytesSaved                                                                           
 - User downloads: UserLargeFileDownloadDuration, UserExperimentsProcessed                                                      
 - Lazy loading: LazyDownloadRequests (by workspace/file type)                                                                  
                                                                                                                                
 Critical Files to Modify                                                                                                       
                                                                                                                                
 1. /studio/app/common/core/storage/s3_storage_controller.py (lines 504-594)                                                    
 2. /studio/app/common/core/background/sync_job.py (line 220)                                                                   
 3. /studio/app/common/core/utils/file_reader.py (all read methods)                                                             
 4. /studio/app/common/routers/auth.py (lines 20-70)                                                                            
 5. /studio/app/common/routers/outputs.py (lines 97, 115, 128, 139)                                                             
                                                                                                                                
 New Files to Create                                                                                                            
                                                                                                                                
 1. /studio/app/common/core/storage/file_filter.py - Pattern-based filtering                                                    
 2. /studio/app/common/core/storage/user_file_downloader.py - Background user downloads                                         
                                                                                                                                
 Data Model Changes                                                                                                             
                                                                                                                                
 NONE REQUIRED - Existing schema supports this:                                                                                 
 - ExperimentRecord.local_sync_status - tracks sync state                                                                       
 - ExperimentRecord.publish_status - identifies published experiments                                                           
 - Workspace.user_id - identifies workspace owner                                                                               
                                                                                                                                                                                                                                                      
 Testing                                                                                                                        
                                                                                                                                
 - Unit tests: Pattern matching, file filtering logic                                                                           
 - Integration tests: End-to-end selective sync, lazy download fallback                                                         
 - Manual: Dataview loads, plots display, login speed, background downloads   