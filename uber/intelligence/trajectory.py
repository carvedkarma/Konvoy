"""
Driver Trajectory Analyzer v3.0
Tracks driver movements, predicts destinations, and detects flow patterns
Includes: Heat map, inflow/outflow rates, dwell time, hotspot detection
Fixes: Dwell time bug, occupancy from trajectories, EWMA smoothing,
       time-windowed flows, destination confidence gating
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
    predicted_dest_confidence: float = 0.0
    heading_deg: float = 0
    avg_speed_ms: float = 0
    last_updated: datetime = field(default_factory=datetime.now)
    
    zone_entry_time: Optional[datetime] = None
    total_dwell_time_sec: float = 0
    zones_visited: List[str] = field(default_factory=list)
    
    smoothed_speed_ms: float = 0.0
    smoothed_heading_deg: float = 0.0
    
    confidence: float = 0.5
    
    def add_point(self, point: TrackPoint) -> Optional[Tuple[str, float]]:
        old_zone = self.current_zone
        old_zone_dwell = None
        
        if self.current_zone != point.zone_id:
            if self.current_zone and self.zone_entry_time:
                old_zone_dwell = (point.timestamp - self.zone_entry_time).total_seconds()
                self.total_dwell_time_sec += old_zone_dwell
            
            if point.zone_id not in self.zones_visited:
                self.zones_visited.append(point.zone_id)
        
        self.points.append(point)
        self.last_updated = point.timestamp
        
        if self.current_zone != point.zone_id:
            self.zone_entry_time = point.timestamp
        
        self.current_zone = point.zone_id
        
        if len(self.points) > 50:
            self.points = self.points[-50:]
        
        if len(self.points) >= 2:
            self._compute_velocity()
        
        if old_zone and old_zone != point.zone_id:
            return (old_zone, old_zone_dwell or 0)
        return None
    
    def _compute_velocity(self):
        if len(self.points) < 2:
            return
        
        p1 = self.points[-2]
        p2 = self.points[-1]
        
        time_diff = (p2.timestamp - p1.timestamp).total_seconds()
        if time_diff <= 0:
            return
        
        distance = haversine_m(p1.lat, p1.lng, p2.lat, p2.lng)
        instant_speed = distance / time_diff
        
        alpha = 0.35
        self.smoothed_speed_ms = alpha * instant_speed + (1 - alpha) * self.smoothed_speed_ms
        self.avg_speed_ms = self.smoothed_speed_ms
        
        new_heading = calculate_bearing(p1.lat, p1.lng, p2.lat, p2.lng)
        
        heading_diff = new_heading - self.smoothed_heading_deg
        if heading_diff > 180:
            heading_diff -= 360
        elif heading_diff < -180:
            heading_diff += 360
        
        self.smoothed_heading_deg = (self.smoothed_heading_deg + alpha * heading_diff) % 360
        self.heading_deg = self.smoothed_heading_deg
    
    def get_trail(self, max_points: int = 20) -> List[Tuple[float, float]]:
        return [(p.lat, p.lng) for p in self.points[-max_points:]]
    
    def get_current_dwell_time(self) -> float:
        if self.zone_entry_time:
            return (datetime.now() - self.zone_entry_time).total_seconds()
        return 0
    
    def has_stable_heading(self, window: int = 3, tolerance_deg: float = 30) -> bool:
        if len(self.points) < window + 1:
            return False
        
        recent_headings = []
        for i in range(-window, 0):
            if i + 1 == 0:
                p1, p2 = self.points[i - 1], self.points[i]
            else:
                p1, p2 = self.points[i], self.points[i + 1]
            heading = calculate_bearing(p1.lat, p1.lng, p2.lat, p2.lng)
            recent_headings.append(heading)
        
        if not recent_headings:
            return False
        
        avg_heading = recent_headings[0]
        for h in recent_headings[1:]:
            diff = h - avg_heading
            if diff > 180:
                diff -= 360
            elif diff < -180:
                diff += 360
            if abs(diff) > tolerance_deg:
                return False
        
        return True


@dataclass
class ZoneMetrics:
    zone_id: str
    current_driver_count: int = 0
    inflow_rate: float = 0.0
    outflow_rate: float = 0.0
    net_flow: float = 0.0
    avg_dwell_time_sec: float = 0.0
    
    inflow_history: List[Tuple[datetime, int]] = field(default_factory=list)
    outflow_history: List[Tuple[datetime, int]] = field(default_factory=list)
    count_history: List[Tuple[datetime, int]] = field(default_factory=list)
    dwell_times: List[float] = field(default_factory=list)
    
    heat_score: float = 0.0
    last_updated: datetime = field(default_factory=datetime.now)
    
    def update_flow_rates(self, window_minutes: int = 5):
        now = datetime.now()
        cutoff = now - timedelta(minutes=window_minutes)
        
        recent_inflow = sum(count for ts, count in self.inflow_history if ts >= cutoff)
        recent_outflow = sum(count for ts, count in self.outflow_history if ts >= cutoff)
        
        self.inflow_rate = recent_inflow / window_minutes
        self.outflow_rate = recent_outflow / window_minutes
        self.net_flow = self.inflow_rate - self.outflow_rate
        
        if self.dwell_times:
            recent_dwells = self.dwell_times[-50:]
            self.avg_dwell_time_sec = sum(recent_dwells) / len(recent_dwells)
        
        self._cleanup_old_data(cutoff)
        self.last_updated = now
    
    def _cleanup_old_data(self, cutoff: datetime):
        self.inflow_history = [(ts, c) for ts, c in self.inflow_history if ts >= cutoff]
        self.outflow_history = [(ts, c) for ts, c in self.outflow_history if ts >= cutoff]
        self.count_history = [(ts, c) for ts, c in self.count_history if ts >= cutoff]
        
        if len(self.dwell_times) > 100:
            self.dwell_times = self.dwell_times[-100:]
    
    def record_inflow(self, count: int = 1):
        self.inflow_history.append((datetime.now(), count))
    
    def record_outflow(self, count: int = 1, dwell_time: float = 0):
        self.outflow_history.append((datetime.now(), count))
        if dwell_time > 0:
            self.dwell_times.append(dwell_time)
    
    def calculate_heat_score(self, driver_count: int) -> float:
        driver_weight = min(1.0, driver_count / 20)
        inflow_weight = min(1.0, self.inflow_rate / 5)
        dwell_weight = min(1.0, self.avg_dwell_time_sec / 300) if self.avg_dwell_time_sec > 0 else 0
        
        self.heat_score = (
            0.4 * driver_weight +
            0.35 * inflow_weight +
            0.25 * dwell_weight
        )
        return self.heat_score


class TrajectoryAnalyzer:
    ZONE_CENTERS = {
        'perth_cbd': (-31.9505, 115.8605),
        'northbridge': (-31.9440, 115.8579),
        'subiaco': (-31.9490, 115.8270),
        'fremantle': (-32.0569, 115.7467),
        'south_fremantle': (-32.0700, 115.7500),
        'north_fremantle': (-32.0350, 115.7450),
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
        'east_perth': (-31.9550, 115.8700),
        'west_perth': (-31.9480, 115.8430),
        'leederville': (-31.9360, 115.8410),
        'cottesloe': (-31.9990, 115.7570),
        'applecross': (-32.0100, 115.8420),
    }
    
    DESTINATION_KEYWORDS = {
        'airport': ['airport', 'terminal'],
        'cbd': ['cbd', 'perth_cbd', 'city'],
        'fremantle': ['fremantle', 'freo'],
        'northbridge': ['northbridge'],
    }
    
    MIN_DEST_CONFIDENCE = 0.55
    
    def __init__(self):
        self.trajectories: Dict[str, DriverTrajectory] = {}
        self.zone_flows: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.active_flows: Dict[str, List[str]] = defaultdict(list)
        self._last_cleanup = datetime.now()
        
        self.zone_metrics: Dict[str, ZoneMetrics] = {}
        self._flow_event_history: List[dict] = []
    
    def _get_zone_metrics(self, zone_id: str) -> ZoneMetrics:
        if zone_id not in self.zone_metrics:
            self.zone_metrics[zone_id] = ZoneMetrics(zone_id=zone_id)
        return self.zone_metrics[zone_id]
    
    def _compute_zone_occupancy(self, zone_id: str) -> int:
        cutoff = datetime.now() - timedelta(minutes=5)
        count = 0
        for traj in self.trajectories.values():
            if traj.current_zone == zone_id and traj.last_updated >= cutoff:
                count += 1
        return count
    
    def update_driver(self, fingerprint_id: str, vehicle_type: str,
                      lat: float, lng: float, bearing: Optional[float],
                      zone_id: str, timestamp: datetime,
                      confidence: float = 0.5) -> Optional[dict]:
        if confidence < 0.7:
            return None
        
        is_new = fingerprint_id not in self.trajectories
        
        if is_new:
            self.trajectories[fingerprint_id] = DriverTrajectory(
                fingerprint_id=fingerprint_id,
                vehicle_type=vehicle_type,
                confidence=confidence
            )
            metrics = self._get_zone_metrics(zone_id)
            metrics.record_inflow(1)
        
        traj = self.trajectories[fingerprint_id]
        traj.confidence = confidence
        
        point = TrackPoint(
            lat=lat,
            lng=lng,
            bearing=bearing,
            timestamp=timestamp,
            zone_id=zone_id
        )
        
        transition_result = traj.add_point(point)
        
        flow_event = None
        if transition_result:
            old_zone, dwell_time = transition_result
            flow_event = self._record_zone_transition(traj, old_zone, zone_id, timestamp)
            
            old_metrics = self._get_zone_metrics(old_zone)
            old_metrics.record_outflow(1, dwell_time)
            
            new_metrics = self._get_zone_metrics(zone_id)
            new_metrics.record_inflow(1)
        
        dest, dest_conf = self._predict_destination_with_confidence(traj)
        traj.predicted_destination = dest
        traj.predicted_dest_confidence = dest_conf
        
        self._periodic_cleanup()
        self._update_zone_metrics()
        
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
        
        flow_event = {
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
        
        self._flow_event_history.append(flow_event)
        if len(self._flow_event_history) > 500:
            self._flow_event_history = self._flow_event_history[-500:]
        
        return flow_event
    
    def _update_zone_metrics(self):
        for zone_id, metrics in self.zone_metrics.items():
            metrics.update_flow_rates(window_minutes=5)
            occupancy = self._compute_zone_occupancy(zone_id)
            metrics.current_driver_count = occupancy
            metrics.calculate_heat_score(occupancy)
    
    def _predict_destination_with_confidence(self, traj: DriverTrajectory) -> Tuple[Optional[str], float]:
        if len(traj.points) < 3:
            return None, 0
        
        is_freeway = any(kw in (traj.current_zone or '').lower() 
                        for kw in ['fwy', 'freeway', 'hwy'])
        min_speed = 5 if is_freeway else 2
        
        if traj.avg_speed_ms < min_speed:
            return None, 0
        
        if not traj.has_stable_heading(window=3, tolerance_deg=30):
            return None, 0
        
        last_point = traj.points[-1]
        heading = traj.heading_deg
        
        best_dest = None
        best_confidence = 0
        
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
            
            distance_km = haversine_m(last_point.lat, last_point.lng, zone_lat, zone_lng) / 1000
            
            bearing_score = 1 - (bearing_diff / 60)
            distance_score = max(0, 1 - (distance_km / 15))
            
            dest_confidence = bearing_score * 0.6 + distance_score * 0.4
            
            if dest_confidence > best_confidence:
                best_confidence = dest_confidence
                best_dest = zone_id
        
        if best_confidence >= self.MIN_DEST_CONFIDENCE:
            return best_dest, best_confidence
        return None, 0
    
    def _predict_destination(self, traj: DriverTrajectory) -> Optional[str]:
        dest, conf = self._predict_destination_with_confidence(traj)
        return dest
    
    def get_zone_flow_summary(self, minutes: int = 30) -> Dict[str, List[dict]]:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        
        windowed_flows: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        
        for event in self._flow_event_history:
            if event['timestamp'] >= cutoff:
                source = event['source_zone']
                target = event['target_zone']
                windowed_flows[source][target] += 1
        
        result = {}
        for source_zone, targets in windowed_flows.items():
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
                if traj.predicted_dest_confidence >= self.MIN_DEST_CONFIDENCE:
                    drivers.append({
                        'fingerprint_id': fid,
                        'vehicle_type': traj.vehicle_type,
                        'current_zone': traj.current_zone,
                        'speed_kmh': traj.avg_speed_ms * 3.6,
                        'eta_minutes': self._estimate_eta(traj, zone_id),
                        'confidence': traj.predicted_dest_confidence
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
                    'predicted_destination': traj.predicted_destination,
                    'dest_confidence': traj.predicted_dest_confidence,
                    'dwell_time_sec': traj.get_current_dwell_time(),
                    'zones_visited': len(traj.zones_visited)
                })
        
        return trails
    
    def get_flow_to_zone(self, zone_id: str, minutes: int = 30) -> int:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        total = 0
        for event in self._flow_event_history:
            if event['timestamp'] >= cutoff and event['target_zone'] == zone_id:
                total += 1
        return total
    
    def get_hotspots(self, top_n: int = 10) -> List[dict]:
        sorted_zones = sorted(
            self.zone_metrics.values(),
            key=lambda m: m.heat_score,
            reverse=True
        )[:top_n]
        
        return [{
            'zone_id': m.zone_id,
            'heat_score': round(m.heat_score, 3),
            'driver_count': m.current_driver_count,
            'inflow_rate': round(m.inflow_rate, 2),
            'outflow_rate': round(m.outflow_rate, 2),
            'net_flow': round(m.net_flow, 2),
            'avg_dwell_time_sec': round(m.avg_dwell_time_sec, 1),
            'coordinates': self.ZONE_CENTERS.get(m.zone_id)
        } for m in sorted_zones if m.heat_score > 0]
    
    def get_zone_metrics_summary(self) -> Dict[str, dict]:
        return {
            zone_id: {
                'driver_count': m.current_driver_count,
                'inflow_rate': round(m.inflow_rate, 2),
                'outflow_rate': round(m.outflow_rate, 2),
                'net_flow': round(m.net_flow, 2),
                'heat_score': round(m.heat_score, 3),
                'avg_dwell_sec': round(m.avg_dwell_time_sec, 1)
            }
            for zone_id, m in self.zone_metrics.items()
        }
    
    def get_declining_zones(self, threshold: float = -0.5) -> List[dict]:
        declining = [
            m for m in self.zone_metrics.values()
            if m.net_flow < threshold
        ]
        return sorted(
            [{
                'zone_id': m.zone_id,
                'net_flow': round(m.net_flow, 2),
                'current_count': m.current_driver_count,
                'outflow_rate': round(m.outflow_rate, 2)
            } for m in declining],
            key=lambda x: x['net_flow']
        )
    
    def get_accumulating_zones(self, threshold: float = 0.5) -> List[dict]:
        accumulating = [
            m for m in self.zone_metrics.values()
            if m.net_flow > threshold
        ]
        return sorted(
            [{
                'zone_id': m.zone_id,
                'net_flow': round(m.net_flow, 2),
                'current_count': m.current_driver_count,
                'inflow_rate': round(m.inflow_rate, 2)
            } for m in accumulating],
            key=lambda x: -x['net_flow']
        )
    
    def get_recent_flow_events(self, minutes: int = 10) -> List[dict]:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        return [
            {**evt, 'timestamp': evt['timestamp'].isoformat()}
            for evt in self._flow_event_history
            if evt['timestamp'] >= cutoff
        ]
    
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
        
        self._update_zone_metrics()
    
    def get_stats(self) -> dict:
        active_count = len(self.trajectories)
        with_prediction = len([t for t in self.trajectories.values() 
                               if t.predicted_destination and t.predicted_dest_confidence >= self.MIN_DEST_CONFIDENCE])
        
        cutoff = datetime.now() - timedelta(minutes=30)
        recent_flows = len([e for e in self._flow_event_history if e['timestamp'] >= cutoff])
        
        avg_dwell = 0
        dwells = [t.get_current_dwell_time() for t in self.trajectories.values() 
                  if t.zone_entry_time]
        if dwells:
            avg_dwell = sum(dwells) / len(dwells)
        
        return {
            'active_trajectories': active_count,
            'with_predictions': with_prediction,
            'total_flow_events': len(self._flow_event_history),
            'recent_flow_events': recent_flows,
            'zones_tracked': len(self.zone_flows),
            'zones_with_metrics': len(self.zone_metrics),
            'avg_current_dwell_sec': round(avg_dwell, 1)
        }
    
    def reset(self):
        self.trajectories.clear()
        self.zone_flows.clear()
        self.zone_metrics.clear()
        self._flow_event_history.clear()
    
    def reset_window(self) -> dict:
        window_summary = self.get_window_summary()
        
        self.trajectories.clear()
        self.zone_flows.clear()
        self.zone_metrics.clear()
        self._flow_event_history.clear()
        self.active_flows.clear()
        self._last_cleanup = datetime.now()
        
        return window_summary
    
    def get_window_summary(self) -> dict:
        zone_summaries = {}
        for zone_id, metrics in self.zone_metrics.items():
            inflow_count = sum(c for _, c in metrics.inflow_history)
            outflow_count = sum(c for _, c in metrics.outflow_history)
            
            avg_dwell = 0
            if metrics.dwell_times:
                avg_dwell = sum(metrics.dwell_times) / len(metrics.dwell_times)
            
            drivers_in_zone = self._compute_zone_occupancy(zone_id)
            
            has_short_dwell = metrics.avg_dwell_time_sec < 300
            has_high_outflow = metrics.outflow_rate > 0.2
            has_moderate_drivers = 3 <= drivers_in_zone <= 15
            has_balanced_flow = metrics.net_flow > -0.5
            
            if has_short_dwell and has_high_outflow and has_moderate_drivers:
                activity_level = 'HOT'
            elif has_balanced_flow and (has_short_dwell or has_high_outflow or drivers_in_zone >= 2):
                activity_level = 'WARM'
            elif drivers_in_zone == 0 or inflow_count + outflow_count == 0:
                activity_level = 'NO_DATA'
            else:
                activity_level = 'COLD'
            
            zone_summaries[zone_id] = {
                'drivers_seen': drivers_in_zone,
                'inflow_count': inflow_count,
                'outflow_count': outflow_count,
                'avg_dwell_minutes': round(avg_dwell / 60, 1),
                'inflow_rate': round(metrics.inflow_rate, 2),
                'outflow_rate': round(metrics.outflow_rate, 2),
                'net_flow': round(metrics.net_flow, 2),
                'heat_score': round(metrics.heat_score, 3),
                'activity_level': activity_level,
                'confidence': min(1.0, drivers_in_zone / 10) if drivers_in_zone > 0 else 0
            }
        
        if zone_summaries:
            best_zone = max(zone_summaries.items(), 
                           key=lambda x: x[1]['outflow_rate'] if x[1]['activity_level'] == 'HOT' else 0)
            worst_zone = min(zone_summaries.items(),
                            key=lambda x: x[1]['outflow_rate'] if x[1]['drivers_seen'] > 0 else float('inf'))
        else:
            best_zone = (None, {})
            worst_zone = (None, {})
        
        total_drivers = len(self.trajectories)
        total_flow_events = len(self._flow_event_history)
        
        zones_with_data = [z for z, s in zone_summaries.items() if s.get('activity_level') != 'NO_DATA']
        
        if len(zones_with_data) < 2 or total_drivers < 3:
            return {
                'zones': zone_summaries,
                'total_unique_drivers': total_drivers,
                'flow_events': total_flow_events,
                'best_zone': None,
                'worst_zone': None,
                'recommendation': 'COLLECTING',
                'recommendation_confidence': 0,
                'has_sufficient_data': False
            }
        
        should_move = False
        move_confidence = 0
        move_target = None
        
        if best_zone[0] and worst_zone[0] and best_zone[0] != worst_zone[0]:
            best_stats = best_zone[1]
            worst_stats = worst_zone[1]
            
            if best_stats.get('activity_level') == 'HOT' and worst_stats.get('activity_level') in ['COLD', 'WARM']:
                should_move = True
                move_confidence = 0.8
                move_target = best_zone[0]
            elif best_stats.get('activity_level') == 'HOT' and best_stats.get('net_flow', 0) < 0:
                should_move = True
                move_confidence = 0.7
                move_target = best_zone[0]
            elif best_stats.get('outflow_rate', 0) > worst_stats.get('outflow_rate', 0) * 1.5:
                should_move = True
                move_confidence = 0.6
                move_target = best_zone[0]
        
        return {
            'zones': zone_summaries,
            'total_unique_drivers': total_drivers,
            'flow_events': total_flow_events,
            'best_zone': best_zone[0],
            'worst_zone': worst_zone[0],
            'recommendation': 'MOVE' if should_move else 'STAY',
            'recommendation_confidence': move_confidence,
            'move_target': move_target,
            'has_sufficient_data': True
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


_trajectory_analyzer: Optional[TrajectoryAnalyzer] = None


def get_trajectory_analyzer() -> TrajectoryAnalyzer:
    global _trajectory_analyzer
    if _trajectory_analyzer is None:
        _trajectory_analyzer = TrajectoryAnalyzer()
    return _trajectory_analyzer
