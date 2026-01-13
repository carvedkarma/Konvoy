"""
Precision Driver Deduplication Engine v2.0
Multi-factor probabilistic identity matching with:
- Weighted signal scoring (distance, bearing, speed, ETA, time penalty)
- Kalman-like motion prediction
- Track lifecycle management (ACTIVE → MISSING → DEAD)
- Spatial grid indexing for efficient matching
- Confidence-weighted decisions
"""

import math
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum


class TrackState(Enum):
    ACTIVE = "active"
    MISSING = "missing"
    DEAD = "dead"


@dataclass
class DriverSighting:
    lat: float
    lng: float
    bearing: Optional[float]
    vehicle_type: str
    timestamp: datetime
    zone_id: str
    eta_seconds: Optional[float] = None
    confidence: float = 0.5


@dataclass 
class TrackedDriver:
    fingerprint_id: str
    vehicle_type: str
    positions: List[Tuple[float, float, datetime]] = field(default_factory=list)
    bearings: List[float] = field(default_factory=list)
    confidence: float = 0.5
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    zone_id: str = ""
    observation_count: int = 1
    
    state: TrackState = TrackState.ACTIVE
    missing_since: Optional[datetime] = None
    
    velocity_lat: float = 0.0
    velocity_lng: float = 0.0
    last_eta: Optional[float] = None
    last_speed_ms: float = 0.0
    
    grid_cell: Optional[Tuple[int, int]] = None
    
    def get_predicted_position(self, target_time: datetime) -> Tuple[float, float]:
        if not self.positions:
            return (0, 0)
        
        last_pos = self.positions[-1]
        last_lat, last_lng, last_time = last_pos
        
        dt = (target_time - last_time).total_seconds()
        
        predicted_lat = last_lat + self.velocity_lat * dt
        predicted_lng = last_lng + self.velocity_lng * dt
        
        return (predicted_lat, predicted_lng)
    
    def update_velocity(self):
        if len(self.positions) < 2:
            self.velocity_lat = 0
            self.velocity_lng = 0
            return
        
        p1 = self.positions[-2]
        p2 = self.positions[-1]
        
        dt = (p2[2] - p1[2]).total_seconds()
        if dt <= 0:
            return
        
        self.velocity_lat = (p2[0] - p1[0]) / dt
        self.velocity_lng = (p2[1] - p1[1]) / dt
        
        distance = haversine_m(p1[0], p1[1], p2[0], p2[1])
        self.last_speed_ms = distance / dt


class SpatialGrid:
    CELL_SIZE_M = 150
    
    def __init__(self):
        self.cells: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
        
    def _lat_lng_to_cell(self, lat: float, lng: float) -> Tuple[int, int]:
        lat_cell = int((lat + 90) * 111000 / self.CELL_SIZE_M)
        lng_cell = int((lng + 180) * 111000 * math.cos(math.radians(lat)) / self.CELL_SIZE_M)
        return (lat_cell, lng_cell)
    
    def get_adjacent_cells(self, cell: Tuple[int, int], radius: int = 1) -> List[Tuple[int, int]]:
        cx, cy = cell
        cells = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                cells.append((cx + dx, cy + dy))
        return cells
    
    def add_driver(self, fingerprint_id: str, lat: float, lng: float) -> Tuple[int, int]:
        cell = self._lat_lng_to_cell(lat, lng)
        self.cells[cell].add(fingerprint_id)
        return cell
    
    def remove_driver(self, fingerprint_id: str, old_cell: Optional[Tuple[int, int]]):
        if old_cell and fingerprint_id in self.cells.get(old_cell, set()):
            self.cells[old_cell].discard(fingerprint_id)
    
    def update_driver(self, fingerprint_id: str, old_cell: Optional[Tuple[int, int]], 
                      lat: float, lng: float) -> Tuple[int, int]:
        new_cell = self._lat_lng_to_cell(lat, lng)
        if old_cell != new_cell:
            self.remove_driver(fingerprint_id, old_cell)
        self.cells[new_cell].add(fingerprint_id)
        return new_cell
    
    def get_nearby_drivers(self, lat: float, lng: float, radius: int = 1) -> Set[str]:
        cell = self._lat_lng_to_cell(lat, lng)
        nearby = set()
        for adj_cell in self.get_adjacent_cells(cell, radius):
            nearby.update(self.cells.get(adj_cell, set()))
        return nearby


class DriverDeduplicator:
    DEFAULT_COORD_THRESHOLD_M = 100
    DENSE_COORD_THRESHOLD_M = 50
    BEARING_THRESHOLD_DEG = 30
    MAX_SPEED_MS = 25
    
    ACTIVE_TTL_SECONDS = 60
    MISSING_TTL_SECONDS = 180
    DEAD_ARCHIVE_MINUTES = 30
    
    MATCH_THRESHOLD = 0.55
    HIGH_CONFIDENCE_MATCH = 0.75
    
    WEIGHTS = {
        'distance': 0.30,
        'bearing': 0.15,
        'speed': 0.15,
        'trajectory': 0.20,
        'eta': 0.10,
        'time_penalty': 0.10
    }
    
    ZONE_SPEEDS_MS = {
        'cbd': 12, 'perth_cbd': 12, 'northbridge': 12,
        'east_perth': 14, 'west_perth': 14,
        'subiaco': 15, 'leederville': 15, 'victoria_park': 15, 'south_perth': 15,
        'fremantle': 14,
        'airport': 17, 'perth_airport': 17, 'default_suburb': 17,
        'kwinana_fwy': 28, 'mitchell_fwy': 28, 'roe_hwy': 28, 'tonkin_hwy': 28, 'freeway': 28,
    }
    
    def __init__(self):
        self.tracked_drivers: Dict[str, TrackedDriver] = {}
        self.zone_thresholds: Dict[str, int] = {}
        self.spatial_grid = SpatialGrid()
        
        self.dead_archive: Dict[str, TrackedDriver] = {}
        
        self._stats = {
            'matches': 0,
            'new_tracks': 0,
            'resurrections': 0,
            'expired': 0
        }
    
    def set_zone_threshold(self, zone_id: str, threshold_m: int):
        self.zone_thresholds[zone_id] = threshold_m
    
    def get_threshold_for_zone(self, zone_id: str, is_dense: bool = False) -> int:
        if zone_id in self.zone_thresholds:
            return self.zone_thresholds[zone_id]
        return self.DENSE_COORD_THRESHOLD_M if is_dense else self.DEFAULT_COORD_THRESHOLD_M
    
    def _get_zone_speed(self, zone_id: str) -> float:
        zone_lower = zone_id.lower() if zone_id else ''
        if zone_lower in self.ZONE_SPEEDS_MS:
            return self.ZONE_SPEEDS_MS[zone_lower]
        for key in self.ZONE_SPEEDS_MS:
            if key in zone_lower or zone_lower in key:
                return self.ZONE_SPEEDS_MS[key]
        if 'fwy' in zone_lower or 'freeway' in zone_lower or 'hwy' in zone_lower:
            return 28
        if 'cbd' in zone_lower:
            return 12
        return self.MAX_SPEED_MS
    
    def _is_freeway_zone(self, zone_id: str) -> bool:
        if not zone_id:
            return False
        zone_lower = zone_id.lower()
        return any(kw in zone_lower for kw in ['fwy', 'freeway', 'hwy', 'highway', 'motorway'])
    
    def process_observation(self, sighting: DriverSighting, is_dense: bool = False) -> Tuple[str, float, bool]:
        threshold_m = self.get_threshold_for_zone(sighting.zone_id, is_dense)
        
        self._update_track_states()
        
        is_freeway = self._is_freeway_zone(sighting.zone_id)
        grid_radius = 5 if is_freeway else 3
        
        nearby_ids = self.spatial_grid.get_nearby_drivers(sighting.lat, sighting.lng, radius=grid_radius)
        
        match, score = self._find_best_match(sighting, threshold_m, nearby_ids)
        
        if match:
            self._update_driver(match, sighting)
            self._stats['matches'] += 1
            return match.fingerprint_id, match.confidence, False
        
        match, score = self._find_recent_match(sighting, threshold_m)
        if match:
            self._update_driver(match, sighting)
            self._stats['matches'] += 1
            return match.fingerprint_id, match.confidence, False
        
        resurrected = self._try_resurrect(sighting, threshold_m)
        if resurrected:
            self._stats['resurrections'] += 1
            return resurrected.fingerprint_id, resurrected.confidence, False
        
        fingerprint_id = self._create_fingerprint(sighting)
        self._add_new_driver(fingerprint_id, sighting)
        self._stats['new_tracks'] += 1
        return fingerprint_id, 0.5, True
    
    def _find_recent_match(self, sighting: DriverSighting, threshold_m: int) -> Tuple[Optional[TrackedDriver], float]:
        best_match = None
        best_score = 0
        now = sighting.timestamp
        zone_speed = self._get_zone_speed(sighting.zone_id)
        
        for fid, driver in self.tracked_drivers.items():
            if driver.vehicle_type != sighting.vehicle_type:
                continue
            if driver.state == TrackState.DEAD:
                continue
            
            if not driver.positions:
                continue
            last_time = driver.positions[-1][2]
            time_diff = (now - last_time).total_seconds()
            if time_diff < 0 or time_diff > 60:
                continue
            
            last_lat, last_lng, _ = driver.positions[-1]
            distance_to_last = haversine_m(last_lat, last_lng, sighting.lat, sighting.lng)
            
            pred_lat, pred_lng = driver.get_predicted_position(now)
            distance_to_pred = haversine_m(pred_lat, pred_lng, sighting.lat, sighting.lng)
            
            use_pred = len(driver.positions) >= 2 and driver.last_speed_ms > 1
            distance = distance_to_pred if use_pred else distance_to_last
            
            max_allowed = zone_speed * time_diff + threshold_m + 100
            
            if distance > max_allowed:
                continue
            
            score = self._calculate_match_score(driver, sighting, threshold_m)
            
            if score > best_score and score >= 0.40:
                best_score = score
                best_match = driver
        
        return best_match, best_score
    
    def _find_best_match(self, sighting: DriverSighting, threshold_m: int, 
                          candidate_ids: Set[str]) -> Tuple[Optional[TrackedDriver], float]:
        best_match = None
        best_score = 0
        
        for fid in candidate_ids:
            driver = self.tracked_drivers.get(fid)
            if not driver:
                continue
            
            if driver.vehicle_type != sighting.vehicle_type:
                continue
            
            if driver.state == TrackState.DEAD:
                continue
            
            score = self._calculate_match_score(driver, sighting, threshold_m)
            
            if score > best_score and score >= self.MATCH_THRESHOLD:
                best_score = score
                best_match = driver
        
        return best_match, best_score
    
    def _calculate_match_score(self, driver: TrackedDriver, sighting: DriverSighting, 
                                threshold_m: int) -> float:
        if not driver.positions:
            return 0
        
        last_pos = driver.positions[-1]
        last_lat, last_lng, last_time = last_pos
        
        time_diff = (sighting.timestamp - last_time).total_seconds()
        if time_diff < 0:
            return 0
        
        zone_speed = self._get_zone_speed(sighting.zone_id)
        max_distance = zone_speed * time_diff + threshold_m
        
        pred_lat, pred_lng = driver.get_predicted_position(sighting.timestamp)
        distance_to_pred = haversine_m(pred_lat, pred_lng, sighting.lat, sighting.lng)
        distance_to_last = haversine_m(last_lat, last_lng, sighting.lat, sighting.lng)
        
        use_predicted = len(driver.positions) >= 2 and driver.last_speed_ms > 2
        distance_m = distance_to_pred if use_predicted else distance_to_last
        
        if distance_m > max_distance * 1.5:
            return 0
        
        distance_score = max(0, 1 - (distance_m / max(max_distance, 1)))
        
        bearing_score = 1.0
        if sighting.bearing is not None and driver.bearings:
            last_bearing = driver.bearings[-1]
            bearing_diff = abs(sighting.bearing - last_bearing)
            bearing_diff = min(bearing_diff, 360 - bearing_diff)
            bearing_score = max(0, 1 - (bearing_diff / 120))
        
        speed_score = 1.0
        if time_diff > 0 and len(driver.positions) >= 2:
            observed_speed = distance_to_last / time_diff
            expected_speed = driver.last_speed_ms
            
            if expected_speed > 0:
                speed_diff = abs(observed_speed - expected_speed)
                max_speed_diff = zone_speed * 0.5
                speed_score = max(0, 1 - (speed_diff / max_speed_diff))
            elif observed_speed < 3:
                speed_score = 1.0
        
        trajectory_score = 1.0
        if len(driver.positions) >= 2 and time_diff > 0:
            trajectory_score = self._calculate_trajectory_score(driver, sighting)
        
        eta_score = 1.0
        if sighting.eta_seconds is not None and driver.last_eta is not None:
            eta_diff = abs(sighting.eta_seconds - driver.last_eta)
            eta_tolerance = time_diff * 1.5 + 30
            eta_score = max(0, 1 - (eta_diff / max(eta_tolerance, 1)))
        
        time_penalty = 1.0
        if time_diff > self.ACTIVE_TTL_SECONDS:
            decay = (time_diff - self.ACTIVE_TTL_SECONDS) / self.MISSING_TTL_SECONDS
            time_penalty = max(0.3, 1 - decay * 0.5)
        
        total_score = (
            self.WEIGHTS['distance'] * distance_score +
            self.WEIGHTS['bearing'] * bearing_score +
            self.WEIGHTS['speed'] * speed_score +
            self.WEIGHTS['trajectory'] * trajectory_score +
            self.WEIGHTS['eta'] * eta_score +
            self.WEIGHTS['time_penalty'] * time_penalty
        )
        
        return total_score
    
    def _calculate_trajectory_score(self, driver: TrackedDriver, sighting: DriverSighting) -> float:
        if len(driver.positions) < 2:
            return 1.0
        
        p1 = driver.positions[-2]
        p2 = driver.positions[-1]
        
        expected_bearing = calculate_bearing(p1[0], p1[1], p2[0], p2[1])
        actual_bearing = calculate_bearing(p2[0], p2[1], sighting.lat, sighting.lng)
        
        bearing_diff = abs(expected_bearing - actual_bearing)
        bearing_diff = min(bearing_diff, 360 - bearing_diff)
        
        if driver.last_speed_ms < 2:
            return max(0.5, 1 - (bearing_diff / 180))
        
        return max(0, 1 - (bearing_diff / 90))
    
    def _try_resurrect(self, sighting: DriverSighting, threshold_m: int) -> Optional[TrackedDriver]:
        best_match = None
        best_score = 0
        
        cutoff = datetime.now() - timedelta(minutes=5)
        
        for fid, driver in list(self.dead_archive.items()):
            if driver.vehicle_type != sighting.vehicle_type:
                continue
            
            if driver.last_seen < cutoff:
                continue
            
            if not driver.positions:
                continue
            
            last_lat, last_lng, _ = driver.positions[-1]
            distance = haversine_m(last_lat, last_lng, sighting.lat, sighting.lng)
            
            if distance < threshold_m * 3:
                score = max(0, 1 - (distance / (threshold_m * 3))) * driver.confidence
                if score > best_score and score > 0.4:
                    best_score = score
                    best_match = driver
        
        if best_match:
            del self.dead_archive[best_match.fingerprint_id]
            best_match.state = TrackState.ACTIVE
            best_match.missing_since = None
            self._update_driver(best_match, sighting)
            self.tracked_drivers[best_match.fingerprint_id] = best_match
            return best_match
        
        return None
    
    def _update_track_states(self):
        now = datetime.now()
        
        for fid, driver in list(self.tracked_drivers.items()):
            time_since_seen = (now - driver.last_seen).total_seconds()
            
            if driver.state == TrackState.ACTIVE:
                if time_since_seen > self.ACTIVE_TTL_SECONDS:
                    driver.state = TrackState.MISSING
                    driver.missing_since = now
                    driver.confidence *= 0.9
            
            elif driver.state == TrackState.MISSING:
                if time_since_seen > self.MISSING_TTL_SECONDS:
                    driver.state = TrackState.DEAD
                    self.dead_archive[fid] = driver
                    del self.tracked_drivers[fid]
                    self.spatial_grid.remove_driver(fid, driver.grid_cell)
                    self._stats['expired'] += 1
        
        archive_cutoff = now - timedelta(minutes=self.DEAD_ARCHIVE_MINUTES)
        expired_archive = [fid for fid, d in self.dead_archive.items() 
                          if d.last_seen < archive_cutoff]
        for fid in expired_archive:
            del self.dead_archive[fid]
    
    def _update_driver(self, driver: TrackedDriver, sighting: DriverSighting):
        driver.positions.append((sighting.lat, sighting.lng, sighting.timestamp))
        if sighting.bearing is not None:
            driver.bearings.append(sighting.bearing)
        
        driver.last_seen = sighting.timestamp
        driver.observation_count += 1
        driver.zone_id = sighting.zone_id
        
        if sighting.eta_seconds is not None:
            driver.last_eta = sighting.eta_seconds
        
        driver.update_velocity()
        
        driver.state = TrackState.ACTIVE
        driver.missing_since = None
        driver.confidence = min(0.99, driver.confidence + 0.03)
        
        driver.grid_cell = self.spatial_grid.update_driver(
            driver.fingerprint_id, driver.grid_cell, sighting.lat, sighting.lng
        )
        
        if len(driver.positions) > 30:
            driver.positions = driver.positions[-30:]
        if len(driver.bearings) > 30:
            driver.bearings = driver.bearings[-30:]
    
    def _add_new_driver(self, fingerprint_id: str, sighting: DriverSighting):
        grid_cell = self.spatial_grid.add_driver(fingerprint_id, sighting.lat, sighting.lng)
        
        driver = TrackedDriver(
            fingerprint_id=fingerprint_id,
            vehicle_type=sighting.vehicle_type,
            positions=[(sighting.lat, sighting.lng, sighting.timestamp)],
            bearings=[sighting.bearing] if sighting.bearing else [],
            confidence=0.5,
            first_seen=sighting.timestamp,
            last_seen=sighting.timestamp,
            zone_id=sighting.zone_id,
            state=TrackState.ACTIVE,
            grid_cell=grid_cell,
            last_eta=sighting.eta_seconds
        )
        self.tracked_drivers[fingerprint_id] = driver
    
    def _create_fingerprint(self, sighting: DriverSighting) -> str:
        data = f"{sighting.vehicle_type}:{sighting.lat:.5f}:{sighting.lng:.5f}:{sighting.timestamp.isoformat()}"
        return hashlib.md5(data.encode()).hexdigest()[:16]
    
    def get_active_drivers(self) -> List[TrackedDriver]:
        return [d for d in self.tracked_drivers.values() if d.state == TrackState.ACTIVE]
    
    def get_high_confidence_drivers(self, min_confidence: float = 0.7) -> List[TrackedDriver]:
        return [d for d in self.tracked_drivers.values() 
                if d.state == TrackState.ACTIVE and d.confidence >= min_confidence]
    
    def get_driver_count(self) -> int:
        return len([d for d in self.tracked_drivers.values() if d.state == TrackState.ACTIVE])
    
    def get_counts_by_type(self) -> Dict[str, int]:
        counts = {'UberX': 0, 'Comfort': 0, 'XL': 0, 'Black': 0}
        type_mapping = {
            'UBERX': 'UberX', 'COMFORT': 'Comfort', 'XL': 'XL', 'BLACK': 'Black',
            'UberX': 'UberX', 'Comfort': 'Comfort', 'Black': 'Black'
        }
        
        for driver in self.tracked_drivers.values():
            if driver.state != TrackState.ACTIVE:
                continue
            ptype = type_mapping.get(driver.vehicle_type, 'UberX')
            counts[ptype] += 1
        
        return counts
    
    def get_counts_by_zone(self) -> Dict[str, Dict[str, int]]:
        zone_counts = {}
        type_mapping = {
            'UBERX': 'UberX', 'COMFORT': 'Comfort', 'XL': 'XL', 'BLACK': 'Black',
            'UberX': 'UberX', 'Comfort': 'Comfort', 'Black': 'Black'
        }
        
        for driver in self.tracked_drivers.values():
            if driver.state != TrackState.ACTIVE:
                continue
            zone = driver.zone_id or 'unknown'
            if zone not in zone_counts:
                zone_counts[zone] = {'UberX': 0, 'Comfort': 0, 'XL': 0, 'Black': 0}
            ptype = type_mapping.get(driver.vehicle_type, 'UberX')
            zone_counts[zone][ptype] += 1
        
        return zone_counts
    
    def get_recent_drivers(self, minutes: int = 10) -> List[Dict]:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        result = []
        
        for driver in self.tracked_drivers.values():
            if driver.last_seen >= cutoff and driver.positions and driver.state == TrackState.ACTIVE:
                lat, lng, _ = driver.positions[-1]
                result.append({
                    'fingerprint_id': driver.fingerprint_id,
                    'lat': lat,
                    'lng': lng,
                    'bearing': driver.bearings[-1] if driver.bearings else None,
                    'vehicle_type': driver.vehicle_type,
                    'zone_id': driver.zone_id,
                    'confidence': driver.confidence,
                    'observations': driver.observation_count,
                    'last_seen': driver.last_seen,
                    'state': driver.state.value,
                    'speed_ms': driver.last_speed_ms
                })
        
        return result
    
    def reset(self):
        self.tracked_drivers.clear()
        self.dead_archive.clear()
        self.spatial_grid = SpatialGrid()
        self._stats = {'matches': 0, 'new_tracks': 0, 'resurrections': 0, 'expired': 0}
    
    def get_stats(self) -> Dict:
        drivers = list(self.tracked_drivers.values())
        active = [d for d in drivers if d.state == TrackState.ACTIVE]
        missing = [d for d in drivers if d.state == TrackState.MISSING]
        
        return {
            'total': len(active),
            'active': len(active),
            'missing': len(missing),
            'archived': len(self.dead_archive),
            'avg_confidence': sum(d.confidence for d in active) / len(active) if active else 0,
            'high_confidence': len([d for d in active if d.confidence > 0.8]),
            'avg_observations': sum(d.observation_count for d in active) / len(active) if active else 0,
            'by_type': self.get_counts_by_type(),
            'matching_stats': dict(self._stats),
            'grid_cells_used': len([c for c in self.spatial_grid.cells.values() if c])
        }


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lng = math.radians(lng2 - lng1)
    
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c


def calculate_bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lng = math.radians(lng2 - lng1)
    
    x = math.sin(delta_lng) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lng)
    
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360
