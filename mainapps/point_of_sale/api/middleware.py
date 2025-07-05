import json
from django.http import JsonResponse
from django.utils import timezone

class OfflineModeMiddleware:
    """
    Middleware to handle requests when the system is in offline mode.
    
    This middleware:
    1. Checks if the request is for the POS API
    2. If the client indicates it's in offline mode, stores the request for later processing
    3. Returns an appropriate response for offline mode
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Only process POS API requests
        if not request.path.startswith('/pos_api/'):
            return self.get_response(request)
        
        # Check if client is in offline mode
        offline_mode = request.headers.get('X-Offline-Mode', 'false').lower() == 'true'
        
        if offline_mode and request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
            # Store the request for later processing
            self._store_offline_request(request)
            
            # Return a response indicating the request was queued
            return JsonResponse({
                'status': 'queued',
                'message': 'Request queued for processing when online',
                'timestamp': timezone.now().isoformat()
            }, status=202)
        
        # Process normally if not in offline mode or for GET requests
        return self.get_response(request)
    
    def _store_offline_request(self, request):
        """Store the request for later processing"""
        from .models import SyncManager
        
        try:
            # Get the request body
            body = json.loads(request.body) if request.body else {}
            
            # Store the request
            offline_request = {
                'path': request.path,
                'method': request.method,
                'body': body,
                'headers': dict(request.headers),
                'timestamp': timezone.now().isoformat()
            }
            
            # Get or create the sync manager
            device_id = request.headers.get('X-Device-ID')
            if device_id:
                sync_manager, _ = SyncManager.objects.get_or_create(device_identifier=device_id)
                
                # Update sync state
                sync_state = sync_manager.sync_state or {}
                pending_requests = sync_state.get('pending_requests', [])
                pending_requests.append(offline_request)
                sync_state['pending_requests'] = pending_requests
                
                sync_manager.sync_state = sync_state
                sync_manager.pending_operations += 1
                sync_manager.save()
        
        except Exception as e:
            # Log the error but don't crash
            print(f"Error storing offline request: {e}")
