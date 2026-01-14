"""
Precision Driver Deduplication Engine v3.0
Multi-factor probabilistic identity matching with:
- Random UUID fingerprints (identity placeholders)
- Prediction-before-dedup matching
- Speed-aware dynamic thresholds
- One-to-one assignment per cycle (greedy Hungarian)
- Kalman-like motion prediction
- Track lifecycle with confidence decay
- Spatial grid indexing with speed-adaptive radius
- Cross-grid identity carryover
"""

import math
import uuid
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
    
    smoothed_speed_ms: float = 0.0
    smoothed_heading: float = 0.0
    
    def get_predicted_position(self, target_time: datetime) -> Tuple[float, float]:
        if not self.positions:
            return (0, 0)
        
        last_pos = self.positions[-1]
        last_lat, last_lng, last_time = last_pos
        
        dt = (target_time - last_time).total_seconds()
        dt = min(dt, 60)
        
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
        instant_speed = distance / dt
        
        alpha = 0.35
        self.smoothed_speed_ms = alpha * instant_speed + (1 - alpha) * self.smoothed_speed_ms
        self.last_speed_ms = self.smoothed_speed_ms
        
        if len(self.bearings) >= 2:
            new_heading = calculate_bearing(p1[0], p1[1], p2[0], p2[1])
            heading_diff = new_heading - self.smoothed_heading
            if heading_diff > 180:
                heading_diff -= 360
            elif heading_diff < -180:
                heading_diff += 360
            self.smoothed_heading = (self.smoothed_heading + alpha * heading_diff) % 360


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
    DEFAULT_COORD_THRESHOLD_M = 150
    DENSE_COORD_THRESHOLD_M = 80
    BEARING_THRESHOLD_DEG = 45
    MAX_SPEED_MS = 28
    
    ACTIVE_TTL_SECONDS = 45
    MISSING_TTL_SECONDS = 90
    DEAD_ARCHIVE_MINUTES = 10
    
    MATCH_THRESHOLD = 0.45
    FAST_MOVER_THRESHOLD = 0.35
    HIGH_CONFIDENCE_MATCH = 0.70
    
    FAST_SPEED_MS = 12
    
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
        'east_perth': 14, 'west_perth': 14, 'elizabeth_quay': 12,
        'subiaco': 15, 'leederville': 15, 'victoria_park': 15, 'south_perth': 15,
        'fremantle': 14, 'south_fremantle': 14, 'north_fremantle': 14, 'fremantle_port': 12,
        'armadale': 15, 'rockingham': 15, 'cannington': 15, 'success': 17,
        'midland': 15, 'girrawheen': 17, 'heathridge': 17,
        'airport': 17, 'perth_airport': 17, 'default_suburb': 17,
        'kwinana_fwy': 28, 'mitchell_fwy': 28, 'roe_hwy': 28, 'tonkin_hwy': 28, 'freeway': 28,
    }
    
    GRID_VISIBILITY_KM = {
        'perth_cbd': 0.8, 'northbridge': 0.8, 'cbd': 0.8,
        'fremantle': 0.9, 'south_fremantle': 0.9, 'north_fremantle': 0.9,
        'perth_airport': 1.2, 'airport': 1.2,
        'default': 1.0
    }
    
    def __init__(self):
        self.tracked_drivers: Dict[str, TrackedDriver] = {}
        self.zone_thresholds: Dict[str, int] = {}
        self.spatial_grid = SpatialGrid()
        
        self.dead_archive: Dict[str, TrackedDriver] = {}
        
        self.cross_grid_cache: Dict[str, Tuple[str, float, float, datetime]] = {}
        self.CROSS_GRID_TTL_SECONDS = 90
        
        self._seen_fingerprints_this_cycle: Set[str] = set()
        self._current_cycle_zone: Optional[str] = None
        
        self._stats = {
            'matches': 0,
            'new_tracks': 0,
            'resurrections': 0,
            'expired': 0,
            'cycle_deduped': 0
        }
    
    def start_cycle(self, zone_id: Optional[str] = None):
        self._seen_fingerprints_this_cycle.clear()
        self._current_cycle_zone = zone_id
    
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
    
    def _get_speed_adaptive_grid_radius(self, zone_id: str, driver_speed_ms: float = 0) -> int:
        is_freeway = self._is_freeway_zone(zone_id)
        
        if is_freeway:
            return 10
        
        if driver_speed_ms > 20:
            return 8
        elif driver_speed_ms > 15:
            return 7
        elif driver_speed_ms > 10:
            return 6
        else:
            return 5
    
    def _get_dynamic_match_threshold(self, driver: TrackedDriver) -> float:
        if driver.last_speed_ms > self.FAST_SPEED_MS:
            return self.FAST_MOVER_THRESHOLD
        return self.MATCH_THRESHOLD
    
    def _apply_confidence_decay(self, driver: TrackedDriver):
        if driver.last_speed_ms > self.FAST_SPEED_MS:
            driver.confidence *= 0.85
        else:
            driver.confidence *= 0.9
    
    def process_batch(self, sightings: List[DriverSighting], is_dense: bool = False) -> List[Tuple[str, float, bool]]:
        if not sightings:
            return []
        
        zone_id = sightings[0].zone_id if sightings else None
        self.start_cycle(zone_id)
        
        self._update_track_states()
        
        results = []
        matched_drivers: Set[str] = set()
        unmatched_sightings: List[Tuple[int, DriverSighting]] = []
        
        all_candidates: List[Tuple[int, DriverSighting, List[Tuple[TrackedDriver, float]]]] = []
        
        for idx, sighting in enumerate(sightings):
            threshold_m = self.get_threshold_for_zone(sighting.zone_id, is_dense)
            
            is_freeway = self._is_freeway_zone(sighting.zone_id)
            grid_radius = self._get_speed_adaptive_grid_radius(sighting.zone_id)
            
            nearby_ids = self.spatial_grid.get_nearby_drivers(sighting.lat, sighting.lng, radius=grid_radius)
            
            candidates = []
            for fid in nearby_ids:
                driver = self.tracked_drivers.get(fid)
                if not driver or driver.vehicle_type != sighting.vehicle_type:
                    continue
                if driver.state == TrackState.DEAD:
                    continue
                
                dynamic_threshold = self._get_dynamic_match_threshold(driver)
                score = self._calculate_match_score(driver, sighting, threshold_m)
                
                if score >= dynamic_threshold:
                    candidates.append((driver, score))
            
            all_candidates.append((idx, sighting, candidates))
        
        all_candidates.sort(key=lambda x: max([s for _, s in x[2]], default=0), reverse=True)
        
        for idx, sighting, candidates in all_candidates:
            best_match = None
            best_score = 0
            
            for driver, score in sorted(candidates, key=lambda x: -x[1]):
                if driver.fingerprint_id not in matched_drivers:
                    best_match = driver
                    best_score = score
                    break
            
            if best_match:
                if best_match.fingerprint_id in self._seen_fingerprints_this_cycle:
                    self._stats['cycle_deduped'] += 1
                    results.append((best_match.fingerprint_id, best_match.confidence, False))
                    continue
                
                matched_drivers.add(best_match.fingerprint_id)
                self._seen_fingerprints_this_cycle.add(best_match.fingerprint_id)
                self._update_driver(best_match, sighting)
                self._stats['matches'] += 1
                results.append((best_match.fingerprint_id, best_match.confidence, False))
            else:
                unmatched_sightings.append((idx, sighting))
        
        for idx, sighting in unmatched_sightings:
            threshold_m = self.get_threshold_for_zone(sighting.zone_id, is_dense)
            
            fallback, score = self._find_recent_match(sighting, threshold_m, matched_drivers)
            if fallback:
                if fallback.fingerprint_id in self._seen_fingerprints_this_cycle:
                    self._stats['cycle_deduped'] += 1
                    results.append((fallback.fingerprint_id, fallback.confidence, False))
                    continue
                    
                matched_drivers.add(fallback.fingerprint_id)
                self._seen_fingerprints_this_cycle.add(fallback.fingerprint_id)
                self._update_driver(fallback, sighting)
                self._stats['matches'] += 1
                results.append((fallback.fingerprint_id, fallback.confidence, False))
                continue
            
            cross_grid = self._check_cross_grid_cache(sighting)
            if cross_grid:
                if cross_grid.fingerprint_id in self._seen_fingerprints_this_cycle:
                    self._stats['cycle_deduped'] += 1
                    results.append((cross_grid.fingerprint_id, cross_grid.confidence, False))
                    continue
                    
                matched_drivers.add(cross_grid.fingerprint_id)
                self._seen_fingerprints_this_cycle.add(cross_grid.fingerprint_id)
                self._update_driver(cross_grid, sighting)
                results.append((cross_grid.fingerprint_id, cross_grid.confidence, False))
                continue
            
            resurrected = self._try_resurrect(sighting, threshold_m)
            if resurrected:
                if resurrected.fingerprint_id in self._seen_fingerprints_this_cycle:
                    self._stats['cycle_deduped'] += 1
                    results.append((resurrected.fingerprint_id, resurrected.confidence, False))
                    continue
                    
                matched_drivers.add(resurrected.fingerprint_id)
                self._seen_fingerprints_this_cycle.add(resurrected.fingerprint_id)
                self._stats['resurrections'] += 1
                results.append((resurrected.fingerprint_id, resurrected.confidence, False))
                continue
            
            fingerprint_id = self._create_fingerprint()
            self._add_new_driver(fingerprint_id, sighting)
            self._seen_fingerprints_this_cycle.add(fingerprint_id)
            self._stats['new_tracks'] += 1
            results.append((fingerprint_id, 0.5, True))
        
        return results
    
    def process_observation(self, sighting: DriverSighting, is_dense: bool = False) -> Tuple[str, float, bool]:
        results = self.process_batch([sighting], is_dense)
        return results[0] if results else (self._create_fingerprint(), 0.5, True)
    
    def _check_cross_grid_cache(self, sighting: DriverSighting) -> Optional[TrackedDriver]:
        now = sighting.timestamp
        cutoff = now - timedelta(seconds=self.CROSS_GRID_TTL_SECONDS)
        
        expired = [k for k, v in self.cross_grid_cache.items() if v[3] < cutoff]
        for k in expired:
            del self.cross_grid_cache[k]
        
        best_match = None
        best_distance = float('inf')
        
        for fid, (vtype, lat, lng, ts) in self.cross_grid_cache.items():
            if vtype != sighting.vehicle_type:
                continue
            
            distance = haversine_m(lat, lng, sighting.lat, sighting.lng)
            time_diff = (now - ts).total_seconds()
            
            max_allowed = self._get_zone_speed(sighting.zone_id) * time_diff + 150
            
            if distance < max_allowed and distance < best_distance:
                driver = self.tracked_drivers.get(fid)
                if driver and driver.state != TrackState.DEAD:
                    best_match = driver
                    best_distance = distance
        
        return best_match
    
    def _update_cross_grid_cache(self, driver: TrackedDriver):
        if driver.positions:
            lat, lng, ts = driver.positions[-1]
            self.cross_grid_cache[driver.fingerprint_id] = (
                driver.vehicle_type, lat, lng, ts
            )
    
    def _find_recent_match(self, sighting: DriverSighting, threshold_m: int,
                           excluded_ids: Set[str] = None) -> Tuple[Optional[TrackedDriver], float]:
        best_match = None
        best_score = 0
        now = sighting.timestamp
        zone_speed = self._get_zone_speed(sighting.zone_id)
        excluded = excluded_ids or set()
        
        for fid, driver in self.tracked_drivers.items():
            if fid in excluded:
                continue
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
            
            pred_lat, pred_lng = driver.get_predicted_position(now)
            distance_to_pred = haversine_m(pred_lat, pred_lng, sighting.lat, sighting.lng)
            
            max_allowed = zone_speed * time_diff + threshold_m + 100
            
            if distance_to_pred > max_allowed:
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
            
            dynamic_threshold = self._get_dynamic_match_threshold(driver)
            score = self._calculate_match_score(driver, sighting, threshold_m)
            
            if score > best_score and score >= dynamic_threshold:
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
        
        base_threshold = threshold_m
        speed_allowance = driver.last_speed_ms * time_diff * 1.5
        max_distance = max(base_threshold + speed_allowance, 200)
        max_distance = min(max_distance, 600)
        
        pred_lat, pred_lng = driver.get_predicted_position(sighting.timestamp)
        distance_to_pred = haversine_m(pred_lat, pred_lng, sighting.lat, sighting.lng)
        distance_to_last = haversine_m(last_lat, last_lng, sighting.lat, sighting.lng)
        
        use_predicted = len(driver.positions) >= 2 and driver.last_speed_ms > 2
        distance_m = distance_to_pred if use_predicted else distance_to_last
        
        if distance_m > max_distance * 2.0:
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
            return max(0.6, 1 - (bearing_diff / 180))
        
        return max(0.3, 1 - (bearing_diff / 120))
    
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
            best_match.confidence = min(best_match.confidence, 0.6)
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
                    self._apply_confidence_decay(driver)
                    self._update_cross_grid_cache(driver)
            
            elif driver.state == TrackState.MISSING:
                if time_since_seen > self.MISSING_TTL_SECONDS:
                    driver.state = TrackState.DEAD
                    self.dead_archive[fid] = driver
                    del self.tracked_drivers[fid]
                    self.spatial_grid.remove_driver(fid, driver.grid_cell)
                    if fid in self.cross_grid_cache:
                        del self.cross_grid_cache[fid]
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
        
        self._update_cross_grid_cache(driver)
        
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
        self._update_cross_grid_cache(driver)
    
    def _create_fingerprint(self, sighting: DriverSighting = None) -> str:
        return uuid.uuid4().hex[:16]
    
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
        self.cross_grid_cache.clear()
        self.spatial_grid = SpatialGrid()
        self._seen_fingerprints_this_cycle.clear()
        self._stats = {'matches': 0, 'new_tracks': 0, 'resurrections': 0, 'expired': 0, 'cycle_deduped': 0}
    
    def get_stats(self) -> Dict:
        drivers = list(self.tracked_drivers.values())
        active = [d for d in drivers if d.state == TrackState.ACTIVE]
        missing = [d for d in drivers if d.state == TrackState.MISSING]
        
        return {
            'total': len(active),
            'active': len(active),
            'missing': len(missing),
            'archived': len(self.dead_archive),
            'cross_grid_cache': len(self.cross_grid_cache),
            'avg_confidence': sum(d.confidence for d in active) / len(active) if active else 0,
            'high_confidence': len([d for d in active if d.confidence > 0.8]),
            'avg_observations': sum(d.observation_count for d in active) / len(active) if active else 0,
            'by_type': self.get_counts_by_type(),
            'matching_stats': dict(self._stats),
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
