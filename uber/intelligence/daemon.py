"""
24/7 Intelligence Daemon v3.0
Continuous background scanning with:
- Reduced polling (2 polls per coordinate)
- Interleaved zone scanning
- Batch processing
- High-confidence trajectory tracking
"""

import time
import uuid
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from collections import defaultdict

from .grid import PERTH_GRID, GridPoint
from .dedup import DriverDeduplicator, DriverSighting
from .trajectory import get_trajectory_analyzer


class IntelligenceDaemon:
    POLLS_PER_COORDINATE = 2
    POLL_INTERVAL_SEC = 2
    CYCLE_PAUSE_SEC = 5
    
    MAX_FETCH_RETRIES = 3
    FETCH_RETRY_DELAY = 3
    WATCHDOG_INTERVAL = 60
    
    REPORT_INTERVAL_MIN = 15
    
    MIN_TRAJECTORY_CONFIDENCE = 0.7
    
    def __init__(self, fetch_drivers_func: Callable, flask_app=None):
        self.fetch_drivers = fetch_drivers_func
        self.flask_app = flask_app
        self.deduplicator = DriverDeduplicator()
        self.trajectory_analyzer = get_trajectory_analyzer()
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._report_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_heartbeat = datetime.now()
        
        self.current_batch_id: Optional[str] = None
        self.current_zone: Optional[str] = None
        self.current_coordinate_index = 0
        self.current_poll_count = 0
        self.coordinates_scanned = 0
        self.total_observations = 0
        self.cycle_count = 0
        self.last_error: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.consecutive_errors = 0
        
        self._last_report_time: Optional[datetime] = None
        self._cycles_since_report = 0
        self._period_driver_samples: List[int] = []
        self._last_window_summary: Optional[dict] = None
        
        self._callbacks: Dict[str, List[Callable]] = {
            'on_observation': [],
            'on_cycle_complete': [],
            'on_error': [],
            'on_batch_complete': [],
            'on_flow_event': [],
            'on_activity_report': []
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
        
        self._thread = threading.Thread(target=self._run_with_recovery, daemon=False)
        self._thread.start()
        
        self._watchdog_thread = threading.Thread(target=self._run_watchdog, daemon=True)
        self._watchdog_thread.start()
        
        self._report_thread = threading.Thread(target=self._run_report_timer, daemon=True)
        self._report_thread.start()
        
        self._last_report_time = self._get_last_slot_time()
        
        return True
    
    def stop(self):
        if not self.is_running:
            return False
        
        self._stop_event.set()
        self.is_running = False
        
        if self._thread:
            self._thread.join(timeout=10)
        
        return True
    
    def _run_with_recovery(self):
        restart_delay = 5
        max_restart_delay = 300
        consecutive_failures = 0
        
        while not self._stop_event.is_set():
            try:
                self._run_loop()
                consecutive_failures = 0
                restart_delay = 5
            except Exception as e:
                consecutive_failures += 1
                self.last_error = f"Recovery restart #{consecutive_failures}: {str(e)}"
                self._emit('on_error', {'error': self.last_error, 'recovery': True})
                
                if self._stop_event.is_set():
                    break
                
                self._stop_event.wait(restart_delay)
                restart_delay = min(restart_delay * 2, max_restart_delay)
        
        self.is_running = False
    
    def _run_watchdog(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(self.WATCHDOG_INTERVAL)
            
            if self._stop_event.is_set():
                break
            
            if not self.is_running:
                continue
            
            heartbeat_age = (datetime.now() - self._last_heartbeat).total_seconds()
            
            if heartbeat_age > self.WATCHDOG_INTERVAL * 3:
                self.last_error = f"Watchdog: No heartbeat for {heartbeat_age:.0f}s, daemon may be stuck"
                self._emit('on_error', {'error': self.last_error, 'watchdog': True})
    
    def _fetch_with_retry(self, lat: float, lng: float) -> list:
        last_error = None
        
        for attempt in range(self.MAX_FETCH_RETRIES):
            try:
                drivers = self.fetch_drivers(lat, lng)
                self.consecutive_errors = 0
                return drivers if drivers else []
            except Exception as e:
                last_error = e
                self.consecutive_errors += 1
                
                if attempt < self.MAX_FETCH_RETRIES - 1:
                    delay = self.FETCH_RETRY_DELAY * (attempt + 1)
                    self._stop_event.wait(delay)
        
        if last_error:
            raise last_error
        return []
    
    def _run_loop(self):
        grid_points = PERTH_GRID.get_all_points()
        
        while not self._stop_event.is_set():
            try:
                interleaved_points = self._interleave_grid_points(grid_points)
                self._run_cycle(interleaved_points)
                self.cycle_count += 1
                self._record_cycle_sample()
                
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
    
    def _interleave_grid_points(self, grid_points: List[GridPoint]) -> List[GridPoint]:
        zone_groups = defaultdict(list)
        for point in grid_points:
            zone_groups[point.zone_id].append(point)
        
        zone_ids = list(zone_groups.keys())
        if not zone_ids:
            return grid_points
        
        interleaved = []
        max_len = max(len(zone_groups[z]) for z in zone_ids)
        
        for i in range(max_len):
            for zone_id in zone_ids:
                if i < len(zone_groups[zone_id]):
                    interleaved.append(zone_groups[zone_id][i])
        
        return interleaved
    
    def _run_cycle(self, grid_points: List[GridPoint]):
        batch_id = str(uuid.uuid4())[:8]
        self.current_batch_id = batch_id
        
        for idx, point in enumerate(grid_points):
            if self._stop_event.is_set():
                break
            
            self.current_coordinate_index = idx
            self.current_zone = point.zone_id
            
            point_sightings = []
            observations = []
            
            for poll in range(self.POLLS_PER_COORDINATE):
                if self._stop_event.is_set():
                    break
                
                self.current_poll_count = poll + 1
                
                try:
                    self._last_heartbeat = datetime.now()
                    drivers = self._fetch_with_retry(point.lat, point.lng)
                    
                    for driver in drivers:
                        sighting = DriverSighting(
                            lat=driver.get('lat', point.lat),
                            lng=driver.get('lng', point.lng),
                            bearing=driver.get('bearing'),
                            vehicle_type=driver.get('product_type', 'UberX'),
                            timestamp=datetime.now(),
                            zone_id=point.zone_id
                        )
                        point_sightings.append(sighting)
                    
                except Exception as e:
                    self.last_error = str(e)
                    self._emit('on_error', {'error': str(e), 'coordinate': idx})
                
                if poll < self.POLLS_PER_COORDINATE - 1:
                    self._stop_event.wait(self.POLL_INTERVAL_SEC)
            
            if point_sightings:
                results = self.deduplicator.process_batch(point_sightings, point.is_dense)
                
                for sighting, (fingerprint_id, confidence, is_new) in zip(point_sightings, results):
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
                    
                    if confidence >= self.MIN_TRAJECTORY_CONFIDENCE:
                        flow_event = self.trajectory_analyzer.update_driver(
                            fingerprint_id=fingerprint_id,
                            vehicle_type=sighting.vehicle_type,
                            lat=sighting.lat,
                            lng=sighting.lng,
                            bearing=sighting.bearing,
                            zone_id=point.zone_id,
                            timestamp=sighting.timestamp,
                            confidence=confidence
                        )
                        
                        if flow_event:
                            self._emit('on_flow_event', flow_event)
                    
                    self.total_observations += 1
            
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
        
        zone_counts = self.deduplicator.get_counts_by_zone()
        top_zones_by_type = {}
        for vtype in ['UberX', 'XL', 'Black']:
            top_zone = None
            top_count = 0
            for zone, counts in zone_counts.items():
                if counts.get(vtype, 0) > top_count:
                    top_count = counts.get(vtype, 0)
                    top_zone = zone
            if top_zone and top_count > 0:
                top_zones_by_type[vtype] = {'zone': top_zone, 'count': top_count}
        
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
            'counts_by_zone': zone_counts,
            'top_zones_by_type': top_zones_by_type,
            'dedup_stats': self.deduplicator.get_stats(),
            'trajectory_stats': self.trajectory_analyzer.get_stats(),
            'last_error': self.last_error,
            'consecutive_errors': self.consecutive_errors,
            'grid_stats': PERTH_GRID.get_stats()
        }
    
    def reset_stats(self):
        self.coordinates_scanned = 0
        self.total_observations = 0
        self.cycle_count = 0
        self.last_error = None
        self.deduplicator.reset()
    
    def _get_last_slot_time(self) -> datetime:
        now = datetime.now()
        minutes = (now.minute // self.REPORT_INTERVAL_MIN) * self.REPORT_INTERVAL_MIN
        return now.replace(minute=minutes, second=0, microsecond=0)
    
    def _get_next_slot_time(self) -> datetime:
        last_slot = self._get_last_slot_time()
        return last_slot + timedelta(minutes=self.REPORT_INTERVAL_MIN)
    
    def _run_report_timer(self):
        while not self._stop_event.is_set():
            now = datetime.now()
            next_slot = self._get_next_slot_time()
            wait_seconds = (next_slot - now).total_seconds()
            
            if wait_seconds > 0:
                self._stop_event.wait(min(wait_seconds + 5, 60))
            
            if self._stop_event.is_set():
                break
            
            now = datetime.now()
            current_slot = self._get_last_slot_time()
            
            if self._last_report_time and current_slot <= self._last_report_time:
                continue
            
            try:
                self._generate_activity_report(current_slot)
                self._reset_window_state()
                self._last_report_time = current_slot
            except Exception as e:
                import traceback
                print(f"[Report] Error generating activity report: {e}")
                traceback.print_exc()
    
    def _generate_activity_report(self, report_time: datetime):
        if not self.flask_app:
            print("[Report] No Flask app context available")
            return
        
        try:
            from uber.models import db, ActivityReport
        except ImportError:
            print("[Report] Cannot import models")
            return
        
        counts = self.deduplicator.get_counts_by_type()
        zone_counts = self.deduplicator.get_counts_by_zone()
        total_drivers = self.deduplicator.get_driver_count()
        
        busiest_zone = None
        busiest_count = 0
        quietest_zone = None
        quietest_count = float('inf')
        
        for zone_id, zcounts in zone_counts.items():
            zone_total = sum(zcounts.values())
            if zone_total > busiest_count:
                busiest_count = zone_total
                busiest_zone = zone_id
            if zone_total < quietest_count:
                quietest_count = zone_total
                quietest_zone = zone_id
        
        if quietest_count == float('inf'):
            quietest_count = 0
        
        avg_per_zone = total_drivers / max(len(zone_counts), 1)
        
        if total_drivers >= 30:
            activity_level = 'very_busy'
        elif total_drivers >= 20:
            activity_level = 'busy'
        elif total_drivers >= 10:
            activity_level = 'moderate'
        elif total_drivers >= 5:
            activity_level = 'quiet'
        else:
            activity_level = 'very_quiet'
        
        time_slot = report_time.strftime('%H:%M')
        day_of_week = report_time.weekday()
        
        try:
            with self.flask_app.app_context():
                prev_report = ActivityReport.query.filter(
                    ActivityReport.report_time < report_time
                ).order_by(ActivityReport.report_time.desc()).first()
                
                change_from_previous = 0
                change_percentage = 0.0
                trend = 'stable'
                
                if prev_report:
                    change_from_previous = total_drivers - prev_report.total_drivers
                    if prev_report.total_drivers > 0:
                        change_percentage = (change_from_previous / prev_report.total_drivers) * 100
                    
                    if change_percentage >= 20:
                        trend = 'surging'
                    elif change_percentage >= 10:
                        trend = 'rising'
                    elif change_percentage <= -20:
                        trend = 'dropping'
                    elif change_percentage <= -10:
                        trend = 'declining'
                    else:
                        trend = 'stable'
                
                import json
                zone_counts_json = json.dumps(zone_counts) if zone_counts else None
                
                report = ActivityReport(
                    report_time=report_time,
                    day_of_week=day_of_week,
                    time_slot=time_slot,
                    total_drivers=total_drivers,
                    uberx_count=counts.get('UberX', 0),
                    comfort_count=counts.get('Comfort', 0),
                    xl_count=counts.get('XL', 0),
                    black_count=counts.get('Black', 0),
                    busiest_zone=busiest_zone,
                    busiest_zone_count=busiest_count,
                    quietest_zone=quietest_zone,
                    quietest_zone_count=int(quietest_count),
                    avg_drivers_per_zone=round(avg_per_zone, 2),
                    activity_level=activity_level,
                    change_from_previous=change_from_previous,
                    change_percentage=round(change_percentage, 1),
                    trend=trend,
                    cycles_in_period=self._cycles_since_report,
                    zone_counts_json=zone_counts_json
                )
                
                db.session.add(report)
                db.session.commit()
                
                self._cycles_since_report = 0
                
                report_data = {
                    'time': time_slot,
                    'day': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][day_of_week],
                    'total_drivers': total_drivers,
                    'activity_level': activity_level,
                    'trend': trend,
                    'change': change_from_previous,
                    'busiest_zone': busiest_zone
                }
                
                self._emit('on_activity_report', report_data)
                
                print(f"[Report] {time_slot} - {total_drivers} drivers ({activity_level}, {trend})")
                
        except Exception as e:
            print(f"[Report] Database error: {e}")
    
    def _record_cycle_sample(self):
        driver_count = self.deduplicator.get_driver_count()
        self._period_driver_samples.append(driver_count)
        self._cycles_since_report += 1
    
    def _reset_window_state(self):
        window_summary = self.trajectory_analyzer.get_window_summary()
        self._last_window_summary = window_summary
        
        self.trajectory_analyzer.reset_window()
        self.deduplicator.reset()
        self._period_driver_samples.clear()
        
        print(f"[Window] Reset complete - {window_summary.get('total_unique_drivers', 0)} drivers cleared, "
              f"{len(window_summary.get('zones', {}))} zones reset")
        
        return window_summary
    
    def get_last_window_summary(self) -> Optional[dict]:
        return self._last_window_summary


_daemon_instance: Optional[IntelligenceDaemon] = None


def get_daemon(fetch_drivers_func: Optional[Callable] = None, flask_app=None) -> Optional[IntelligenceDaemon]:
    global _daemon_instance
    
    if _daemon_instance is None and fetch_drivers_func:
        _daemon_instance = IntelligenceDaemon(fetch_drivers_func, flask_app)
    
    return _daemon_instance


def start_daemon(fetch_drivers_func: Callable, flask_app=None) -> bool:
    global _daemon_instance
    
    if _daemon_instance is None:
        _daemon_instance = IntelligenceDaemon(fetch_drivers_func, flask_app)
    
    return _daemon_instance.start()


def stop_daemon() -> bool:
    global _daemon_instance
    
    if _daemon_instance:
        return _daemon_instance.stop()
    
    return False
