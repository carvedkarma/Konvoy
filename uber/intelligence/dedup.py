"""
Precision Driver Deduplication Engine
Multi-factor fingerprinting with confidence scoring
"""

import math
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class DriverSighting:
    lat: float
    lng: float
    bearing: Optional[float]
    vehicle_type: str
    timestamp: datetime
    zone_id: str
    confidence: float = 0.5


@dataclass 
class TrackedDriver:
    fingerprint_id: str
    vehicle_type: str
    positions: List[Tuple[float, float, datetime]]
    bearings: List[float]
    confidence: float
    first_seen: datetime
    last_seen: datetime
    zone_id: str
    observation_count: int = 1


class DriverDeduplicator:
    DEFAULT_COORD_THRESHOLD_M = 100
    DENSE_COORD_THRESHOLD_M = 50
    BEARING_THRESHOLD_DEG = 30
    MAX_SPEED_MS = 30
    TRAJECTORY_TOLERANCE_M = 100
    MAX_TRACKING_MINUTES = 30
    
    def __init__(self):
        self.tracked_drivers: Dict[str, TrackedDriver] = {}
        self.zone_thresholds: Dict[str, int] = {}
    
    def set_zone_threshold(self, zone_id: str, threshold_m: int):
        self.zone_thresholds[zone_id] = threshold_m
    
    def get_threshold_for_zone(self, zone_id: str, is_dense: bool = False) -> int:
        if zone_id in self.zone_thresholds:
            return self.zone_thresholds[zone_id]
        return self.DENSE_COORD_THRESHOLD_M if is_dense else self.DEFAULT_COORD_THRESHOLD_M
    
    def process_observation(self, sighting: DriverSighting, is_dense: bool = False) -> Tuple[str, float, bool]:
        threshold_m = self.get_threshold_for_zone(sighting.zone_id, is_dense)
        
        self._cleanup_old_drivers()
        
        match = self._find_matching_driver(sighting, threshold_m)
        
        if match:
            self._update_driver(match, sighting)
            return match.fingerprint_id, match.confidence, False
        else:
            fingerprint_id = self._create_fingerprint(sighting)
            self._add_new_driver(fingerprint_id, sighting)
            return fingerprint_id, 0.5, True
    
    def _find_matching_driver(self, sighting: DriverSighting, threshold_m: int) -> Optional[TrackedDriver]:
        best_match = None
        best_score = 0
        
        for driver in self.tracked_drivers.values():
            if driver.vehicle_type != sighting.vehicle_type:
                continue
            
            score = self._calculate_match_score(driver, sighting, threshold_m)
            
            if score > best_score and score >= 0.6:
                best_score = score
                best_match = driver
        
        return best_match
    
    def _calculate_match_score(self, driver: TrackedDriver, sighting: DriverSighting, threshold_m: int) -> float:
        if not driver.positions:
            return 0
        
        last_pos = driver.positions[-1]
        last_lat, last_lng, last_time = last_pos
        
        distance_m = self._haversine_m(last_lat, last_lng, sighting.lat, sighting.lng)
        
        time_diff = (sighting.timestamp - last_time).total_seconds()
        max_distance = self.MAX_SPEED_MS * time_diff + threshold_m
        
        if distance_m > max_distance:
            return 0
        
        distance_score = max(0, 1 - (distance_m / max_distance))
        
        bearing_score = 1.0
        if sighting.bearing is not None and driver.bearings:
            last_bearing = driver.bearings[-1]
            bearing_diff = abs(sighting.bearing - last_bearing)
            bearing_diff = min(bearing_diff, 360 - bearing_diff)
            bearing_score = max(0, 1 - (bearing_diff / 180))
        
        trajectory_score = 1.0
        if len(driver.positions) >= 2 and time_diff > 0:
            trajectory_score = self._calculate_trajectory_score(driver, sighting)
        
        weights = {'distance': 0.5, 'bearing': 0.2, 'trajectory': 0.3}
        total_score = (
            weights['distance'] * distance_score +
            weights['bearing'] * bearing_score +
            weights['trajectory'] * trajectory_score
        )
        
        return total_score
    
    def _calculate_trajectory_score(self, driver: TrackedDriver, sighting: DriverSighting) -> float:
        if len(driver.positions) < 2:
            return 1.0
        
        p1 = driver.positions[-2]
        p2 = driver.positions[-1]
        
        expected_bearing = self._calculate_bearing(p1[0], p1[1], p2[0], p2[1])
        
        actual_bearing = self._calculate_bearing(p2[0], p2[1], sighting.lat, sighting.lng)
        
        bearing_diff = abs(expected_bearing - actual_bearing)
        bearing_diff = min(bearing_diff, 360 - bearing_diff)
        
        return max(0, 1 - (bearing_diff / 90))
    
    def _update_driver(self, driver: TrackedDriver, sighting: DriverSighting):
        driver.positions.append((sighting.lat, sighting.lng, sighting.timestamp))
        if sighting.bearing is not None:
            driver.bearings.append(sighting.bearing)
        driver.last_seen = sighting.timestamp
        driver.observation_count += 1
        
        driver.confidence = min(0.99, driver.confidence + 0.05)
        
        if len(driver.positions) > 20:
            driver.positions = driver.positions[-20:]
        if len(driver.bearings) > 20:
            driver.bearings = driver.bearings[-20:]
    
    def _add_new_driver(self, fingerprint_id: str, sighting: DriverSighting):
        driver = TrackedDriver(
            fingerprint_id=fingerprint_id,
            vehicle_type=sighting.vehicle_type,
            positions=[(sighting.lat, sighting.lng, sighting.timestamp)],
            bearings=[sighting.bearing] if sighting.bearing else [],
            confidence=0.5,
            first_seen=sighting.timestamp,
            last_seen=sighting.timestamp,
            zone_id=sighting.zone_id
        )
        self.tracked_drivers[fingerprint_id] = driver
    
    def _create_fingerprint(self, sighting: DriverSighting) -> str:
        data = f"{sighting.vehicle_type}:{sighting.lat:.5f}:{sighting.lng:.5f}:{sighting.timestamp.isoformat()}"
        return hashlib.md5(data.encode()).hexdigest()[:16]
    
    def _cleanup_old_drivers(self):
        cutoff = datetime.now() - timedelta(minutes=self.MAX_TRACKING_MINUTES)
        expired = [fid for fid, d in self.tracked_drivers.items() if d.last_seen < cutoff]
        for fid in expired:
            del self.tracked_drivers[fid]
    
    def _haversine_m(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        R = 6371000
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lng = math.radians(lng2 - lng1)
        
        a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    def _calculate_bearing(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lng = math.radians(lng2 - lng1)
        
        x = math.sin(delta_lng) * math.cos(lat2_rad)
        y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lng)
        
        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360
    
    def get_active_drivers(self) -> List[TrackedDriver]:
        return list(self.tracked_drivers.values())
    
    def get_driver_count(self) -> int:
        return len(self.tracked_drivers)
    
    def get_counts_by_type(self) -> Dict[str, int]:
        counts = {'UberX': 0, 'Comfort': 0, 'XL': 0, 'Black': 0}
        type_mapping = {
            'UBERX': 'UberX', 'COMFORT': 'Comfort', 'XL': 'XL', 'BLACK': 'Black',
            'UberX': 'UberX', 'Comfort': 'Comfort', 'Black': 'Black'
        }
        
        for driver in self.tracked_drivers.values():
            ptype = type_mapping.get(driver.vehicle_type, 'UberX')
            counts[ptype] += 1
        
        return counts
    
    def reset(self):
        self.tracked_drivers.clear()
    
    def get_stats(self) -> Dict:
        drivers = list(self.tracked_drivers.values())
        
        if not drivers:
            return {'total': 0, 'avg_confidence': 0, 'avg_observations': 0}
        
        return {
            'total': len(drivers),
            'avg_confidence': sum(d.confidence for d in drivers) / len(drivers),
            'avg_observations': sum(d.observation_count for d in drivers) / len(drivers),
            'by_type': self.get_counts_by_type(),
            'high_confidence': len([d for d in drivers if d.confidence > 0.8]),
            'low_confidence': len([d for d in drivers if d.confidence < 0.5])
        }
