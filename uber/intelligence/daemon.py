"""
24/7 Intelligence Daemon
Continuous background scanning with triple-confirmation
"""

import time
import uuid
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable

from .grid import PERTH_GRID, GridPoint
from .dedup import DriverDeduplicator, DriverSighting


class IntelligenceDaemon:
    POLLS_PER_COORDINATE = 3
    POLL_INTERVAL_SEC = 2
    CYCLE_PAUSE_SEC = 5
    
    def __init__(self, fetch_drivers_func: Callable):
        self.fetch_drivers = fetch_drivers_func
        self.deduplicator = DriverDeduplicator()
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        self.current_batch_id: Optional[str] = None
        self.current_zone: Optional[str] = None
        self.current_coordinate_index = 0
        self.current_poll_count = 0
        self.coordinates_scanned = 0
        self.total_observations = 0
        self.cycle_count = 0
        self.last_error: Optional[str] = None
        self.started_at: Optional[datetime] = None
        
        self._callbacks: Dict[str, List[Callable]] = {
            'on_observation': [],
            'on_cycle_complete': [],
            'on_error': [],
            'on_batch_complete': []
        }
    
    def register_callback(self, event: str, callback: Callable):
        if event in self._callbacks:
            self._callbacks[event].append(callback)
    
    def _emit(self, event: str, data: dict):
        for callback in self._callbacks.get(event, []):
            try:
                callback(data)
            except Exception as e:
                print(f"Callback error for {event}: {e}")
    
    def start(self):
        if self.is_running:
            return False
        
        self._stop_event.clear()
        self.is_running = True
        self.started_at = datetime.now()
        self.current_batch_id = str(uuid.uuid4())[:8]
        
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        
        return True
    
    def stop(self):
        if not self.is_running:
            return False
        
        self._stop_event.set()
        self.is_running = False
        
        if self._thread:
            self._thread.join(timeout=10)
        
        return True
    
    def _run_loop(self):
        grid_points = PERTH_GRID.get_all_points()
        
        while not self._stop_event.is_set():
            try:
                self._run_cycle(grid_points)
                self.cycle_count += 1
                
                self._emit('on_cycle_complete', {
                    'cycle': self.cycle_count,
                    'unique_drivers': self.deduplicator.get_driver_count(),
                    'counts': self.deduplicator.get_counts_by_type()
                })
                
                self._stop_event.wait(self.CYCLE_PAUSE_SEC)
                
            except Exception as e:
                self.last_error = str(e)
                self._emit('on_error', {'error': str(e)})
                self._stop_event.wait(30)
    
    def _run_cycle(self, grid_points: List[GridPoint]):
        batch_id = str(uuid.uuid4())[:8]
        self.current_batch_id = batch_id
        
        for idx, point in enumerate(grid_points):
            if self._stop_event.is_set():
                break
            
            self.current_coordinate_index = idx
            self.current_zone = point.zone_id
            
            observations = []
            for poll in range(self.POLLS_PER_COORDINATE):
                if self._stop_event.is_set():
                    break
                
                self.current_poll_count = poll + 1
                
                try:
                    drivers = self.fetch_drivers(point.lat, point.lng)
                    
                    for driver in drivers:
                        sighting = DriverSighting(
                            lat=driver.get('lat', point.lat),
                            lng=driver.get('lng', point.lng),
                            bearing=driver.get('bearing'),
                            vehicle_type=driver.get('product_type', 'UberX'),
                            timestamp=datetime.now(),
                            zone_id=point.zone_id
                        )
                        
                        fingerprint_id, confidence, is_new = self.deduplicator.process_observation(
                            sighting, point.is_dense
                        )
                        
                        observations.append({
                            'fingerprint_id': fingerprint_id,
                            'lat': sighting.lat,
                            'lng': sighting.lng,
                            'bearing': sighting.bearing,
                            'vehicle_type': sighting.vehicle_type,
                            'zone_id': point.zone_id,
                            'confidence': confidence,
                            'is_new': is_new,
                            'batch_id': batch_id,
                            'timestamp': sighting.timestamp
                        })
                        
                        self.total_observations += 1
                    
                except Exception as e:
                    self.last_error = str(e)
                    self._emit('on_error', {'error': str(e), 'coordinate': idx})
                
                if poll < self.POLLS_PER_COORDINATE - 1:
                    self._stop_event.wait(self.POLL_INTERVAL_SEC)
            
            self.coordinates_scanned += 1
            
            if observations:
                self._emit('on_observation', {
                    'zone_id': point.zone_id,
                    'coordinate': {'lat': point.lat, 'lng': point.lng},
                    'observations': observations,
                    'unique_at_point': len(set(o['fingerprint_id'] for o in observations))
                })
    
    def get_status(self) -> Dict:
        uptime = None
        if self.started_at:
            uptime = (datetime.now() - self.started_at).total_seconds()
        
        return {
            'is_running': self.is_running,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'uptime_seconds': uptime,
            'current_batch_id': self.current_batch_id,
            'current_zone': self.current_zone,
            'current_coordinate': self.current_coordinate_index,
            'current_poll': self.current_poll_count,
            'coordinates_scanned': self.coordinates_scanned,
            'total_observations': self.total_observations,
            'cycle_count': self.cycle_count,
            'unique_drivers': self.deduplicator.get_driver_count(),
            'counts_by_type': self.deduplicator.get_counts_by_type(),
            'dedup_stats': self.deduplicator.get_stats(),
            'last_error': self.last_error,
            'grid_stats': PERTH_GRID.get_stats()
        }
    
    def reset_stats(self):
        self.coordinates_scanned = 0
        self.total_observations = 0
        self.cycle_count = 0
        self.last_error = None
        self.deduplicator.reset()


_daemon_instance: Optional[IntelligenceDaemon] = None


def get_daemon(fetch_drivers_func: Optional[Callable] = None) -> Optional[IntelligenceDaemon]:
    global _daemon_instance
    
    if _daemon_instance is None and fetch_drivers_func:
        _daemon_instance = IntelligenceDaemon(fetch_drivers_func)
    
    return _daemon_instance


def start_daemon(fetch_drivers_func: Callable) -> bool:
    global _daemon_instance
    
    if _daemon_instance is None:
        _daemon_instance = IntelligenceDaemon(fetch_drivers_func)
    
    return _daemon_instance.start()


def stop_daemon() -> bool:
    global _daemon_instance
    
    if _daemon_instance:
        return _daemon_instance.stop()
    
    return False
