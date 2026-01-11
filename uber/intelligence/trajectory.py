"""
Driver Trajectory Analyzer
Tracks driver movements, predicts destinations, and detects flow patterns
"""

import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class TrackPoint:
    lat: float
    lng: float
    bearing: Optional[float]
    timestamp: datetime
    zone_id: str


@dataclass
class DriverTrajectory:
    fingerprint_id: str
    vehicle_type: str
    points: List[TrackPoint] = field(default_factory=list)
    current_zone: Optional[str] = None
    predicted_destination: Optional[str] = None
    heading_deg: float = 0
    avg_speed_ms: float = 0
    last_updated: datetime = field(default_factory=datetime.now)
    
    def add_point(self, point: TrackPoint):
        self.points.append(point)
        self.last_updated = point.timestamp
        self.current_zone = point.zone_id
        
        if len(self.points) > 50:
            self.points = self.points[-50:]
        
        if len(self.points) >= 2:
            self._compute_velocity()
    
    def _compute_velocity(self):
        if len(self.points) < 2:
            return
        
        p1 = self.points[-2]
        p2 = self.points[-1]
        
        time_diff = (p2.timestamp - p1.timestamp).total_seconds()
        if time_diff <= 0:
            return
        
        distance = haversine_m(p1.lat, p1.lng, p2.lat, p2.lng)
        self.avg_speed_ms = distance / time_diff
        self.heading_deg = calculate_bearing(p1.lat, p1.lng, p2.lat, p2.lng)
    
    def get_trail(self, max_points: int = 20) -> List[Tuple[float, float]]:
        return [(p.lat, p.lng) for p in self.points[-max_points:]]


class TrajectoryAnalyzer:
    ZONE_CENTERS = {
        'perth_cbd': (-31.9505, 115.8605),
        'northbridge': (-31.9440, 115.8579),
        'subiaco': (-31.9490, 115.8270),
        'fremantle': (-32.0569, 115.7467),
        'perth_airport': (-31.9403, 115.9670),
        'scarborough': (-31.8920, 115.7570),
        'joondalup': (-31.7440, 115.7650),
        'rockingham': (-32.2920, 115.7290),
        'armadale': (-32.1540, 116.0150),
        'midland': (-31.8890, 116.0110),
        'morley': (-31.8890, 115.9050),
        'cannington': (-32.0180, 115.9350),
        'victoria_park': (-31.9760, 115.8970),
        'south_perth': (-31.9760, 115.8640),
        'claremont': (-31.9800, 115.7810),
    }
    
    DESTINATION_KEYWORDS = {
        'airport': ['airport', 'terminal'],
        'cbd': ['cbd', 'perth_cbd', 'city'],
        'fremantle': ['fremantle', 'freo'],
        'northbridge': ['northbridge'],
    }
    
    def __init__(self):
        self.trajectories: Dict[str, DriverTrajectory] = {}
        self.zone_flows: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.active_flows: Dict[str, List[str]] = defaultdict(list)
        self._last_cleanup = datetime.now()
    
    def update_driver(self, fingerprint_id: str, vehicle_type: str,
                      lat: float, lng: float, bearing: Optional[float],
                      zone_id: str, timestamp: datetime) -> Optional[dict]:
        if fingerprint_id not in self.trajectories:
            self.trajectories[fingerprint_id] = DriverTrajectory(
                fingerprint_id=fingerprint_id,
                vehicle_type=vehicle_type
            )
        
        traj = self.trajectories[fingerprint_id]
        old_zone = traj.current_zone
        
        point = TrackPoint(
            lat=lat,
            lng=lng,
            bearing=bearing,
            timestamp=timestamp,
            zone_id=zone_id
        )
        traj.add_point(point)
        
        flow_event = None
        if old_zone and old_zone != zone_id:
            flow_event = self._record_zone_transition(traj, old_zone, zone_id, timestamp)
        
        traj.predicted_destination = self._predict_destination(traj)
        
        self._periodic_cleanup()
        
        return flow_event
    
    def _record_zone_transition(self, traj: DriverTrajectory, 
                                 source: str, target: str,
                                 timestamp: datetime) -> dict:
        self.zone_flows[source][target] += 1
        
        travel_time = None
        distance = None
        if len(traj.points) >= 2:
            first_in_old = None
            for p in traj.points:
                if p.zone_id == source:
                    first_in_old = p
                    break
            if first_in_old:
                travel_time = (timestamp - first_in_old.timestamp).total_seconds()
                distance = haversine_m(first_in_old.lat, first_in_old.lng,
                                       traj.points[-1].lat, traj.points[-1].lng)
        
        return {
            'fingerprint_id': traj.fingerprint_id,
            'vehicle_type': traj.vehicle_type,
            'source_zone': source,
            'target_zone': target,
            'travel_time_sec': travel_time,
            'distance_m': distance,
            'avg_speed_ms': traj.avg_speed_ms,
            'heading_deg': traj.heading_deg,
            'timestamp': timestamp
        }
    
    def _predict_destination(self, traj: DriverTrajectory) -> Optional[str]:
        if len(traj.points) < 3 or traj.avg_speed_ms < 2:
            return None
        
        last_point = traj.points[-1]
        heading = traj.heading_deg
        
        best_dest = None
        best_score = float('inf')
        
        for zone_id, (zone_lat, zone_lng) in self.ZONE_CENTERS.items():
            if zone_id == traj.current_zone:
                continue
            
            bearing_to_zone = calculate_bearing(
                last_point.lat, last_point.lng,
                zone_lat, zone_lng
            )
            
            bearing_diff = abs(heading - bearing_to_zone)
            bearing_diff = min(bearing_diff, 360 - bearing_diff)
            
            if bearing_diff > 60:
                continue
            
            distance = haversine_m(last_point.lat, last_point.lng, zone_lat, zone_lng)
            
            score = distance + (bearing_diff * 100)
            
            if score < best_score:
                best_score = score
                best_dest = zone_id
        
        return best_dest
    
    def get_zone_flow_summary(self, minutes: int = 30) -> Dict[str, List[dict]]:
        result = {}
        
        for source_zone, targets in self.zone_flows.items():
            flows = []
            for target_zone, count in sorted(targets.items(), key=lambda x: -x[1])[:5]:
                flows.append({
                    'target_zone': target_zone,
                    'driver_count': count
                })
            if flows:
                result[source_zone] = flows
        
        return result
    
    def get_drivers_heading_to(self, zone_id: str) -> List[dict]:
        drivers = []
        zone_lower = zone_id.lower()
        
        for fid, traj in self.trajectories.items():
            if traj.predicted_destination and zone_lower in traj.predicted_destination.lower():
                drivers.append({
                    'fingerprint_id': fid,
                    'vehicle_type': traj.vehicle_type,
                    'current_zone': traj.current_zone,
                    'speed_kmh': traj.avg_speed_ms * 3.6,
                    'eta_minutes': self._estimate_eta(traj, zone_id)
                })
        
        return drivers
    
    def _estimate_eta(self, traj: DriverTrajectory, dest_zone: str) -> Optional[float]:
        if dest_zone not in self.ZONE_CENTERS:
            return None
        
        if traj.avg_speed_ms < 1:
            return None
        
        dest_lat, dest_lng = self.ZONE_CENTERS[dest_zone]
        last_point = traj.points[-1]
        distance = haversine_m(last_point.lat, last_point.lng, dest_lat, dest_lng)
        
        return (distance / traj.avg_speed_ms) / 60
    
    def get_active_driver_trails(self, minutes: int = 10) -> List[dict]:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        trails = []
        
        for fid, traj in self.trajectories.items():
            if traj.last_updated < cutoff:
                continue
            
            trail = traj.get_trail(20)
            if len(trail) >= 2:
                trails.append({
                    'fingerprint_id': fid,
                    'vehicle_type': traj.vehicle_type,
                    'trail': trail,
                    'heading': traj.heading_deg,
                    'speed_kmh': traj.avg_speed_ms * 3.6,
                    'current_zone': traj.current_zone,
                    'predicted_destination': traj.predicted_destination
                })
        
        return trails
    
    def get_flow_to_zone(self, zone_id: str) -> int:
        total = 0
        for source, targets in self.zone_flows.items():
            total += targets.get(zone_id, 0)
        return total
    
    def _periodic_cleanup(self):
        now = datetime.now()
        if (now - self._last_cleanup).total_seconds() < 60:
            return
        
        self._last_cleanup = now
        cutoff = now - timedelta(hours=1)
        
        expired = [fid for fid, traj in self.trajectories.items()
                   if traj.last_updated < cutoff]
        for fid in expired:
            del self.trajectories[fid]
    
    def get_stats(self) -> dict:
        active_count = len(self.trajectories)
        with_prediction = len([t for t in self.trajectories.values() 
                               if t.predicted_destination])
        total_flows = sum(sum(targets.values()) 
                         for targets in self.zone_flows.values())
        
        return {
            'active_trajectories': active_count,
            'with_predictions': with_prediction,
            'total_flow_events': total_flows,
            'zones_tracked': len(self.zone_flows)
        }
    
    def reset(self):
        self.trajectories.clear()
        self.zone_flows.clear()


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


_trajectory_analyzer: Optional[TrajectoryAnalyzer] = None


def get_trajectory_analyzer() -> TrajectoryAnalyzer:
    global _trajectory_analyzer
    if _trajectory_analyzer is None:
        _trajectory_analyzer = TrajectoryAnalyzer()
    return _trajectory_analyzer
