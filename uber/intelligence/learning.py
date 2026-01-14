"""
Self-Learning Engine v3.0
Analyzes data to discover patterns, correlations, and predictions
Fixes: Smoothed supply, percent deviation, temporal confirmation,
       confidence gates, higher correlation threshold, ratio-based movement
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import math


class LearningEngine:
    CONFIDENCE_THRESHOLD = 0.6
    ANOMALY_DEVIATION_THRESHOLD = 0.35
    MIN_CORRELATION = 0.7
    MIN_SAMPLES_FOR_CORRELATION = 10
    
    SUPPLY_SMOOTHING_ALPHA = 0.3
    
    def __init__(self, db_session):
        self.db = db_session
        self._hourly_cache: Dict[str, Dict] = {}
        self._pattern_cache: Dict[str, Dict] = {}
        self._correlation_cache: List[Dict] = []
        
        self._expected_values: Dict[str, Dict] = {}
        self._anomaly_history: List[Dict] = []
        self._decision_log: List[Dict] = []
        
        self._smoothed_supply: Dict[str, float] = {}
        self._recent_deviations: Dict[str, List[float]] = defaultdict(list)
    
    def _get_smoothed_supply(self, zone_id: str, raw_count: int) -> float:
        if zone_id not in self._smoothed_supply:
            self._smoothed_supply[zone_id] = float(raw_count)
            return float(raw_count)
        
        alpha = self.SUPPLY_SMOOTHING_ALPHA
        self._smoothed_supply[zone_id] = (
            alpha * raw_count + (1 - alpha) * self._smoothed_supply[zone_id]
        )
        return self._smoothed_supply[zone_id]
    
    def run_hourly_analysis(self):
        from uber.models import DriverObservation, HourlySnapshot, ZoneConfig
        
        now = datetime.now()
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        hour_ago = hour_start - timedelta(hours=1)
        
        observations = DriverObservation.query.filter(
            DriverObservation.observed_at >= hour_ago,
            DriverObservation.observed_at < hour_start
        ).all()
        
        zone_data = defaultdict(lambda: {
            'fingerprints': set(),
            'high_conf_fingerprints': set(),
            'observations': 0,
            'types': defaultdict(int),
            'bearings': [],
            'confidences': []
        })
        
        for obs in observations:
            data = zone_data[obs.zone_id]
            data['fingerprints'].add(obs.fingerprint_id)
            data['observations'] += 1
            data['types'][obs.vehicle_type] += 1
            if obs.bearing is not None:
                data['bearings'].append(obs.bearing)
            data['confidences'].append(obs.confidence)
            
            if obs.confidence >= 0.7:
                data['high_conf_fingerprints'].add(obs.fingerprint_id)
        
        for zone_id, data in zone_data.items():
            avg_bearing = None
            bearing_variance = None
            primary_direction = None
            
            if data['bearings']:
                avg_bearing = sum(data['bearings']) / len(data['bearings'])
                bearing_variance = sum((b - avg_bearing)**2 for b in data['bearings']) / len(data['bearings'])
                primary_direction = self._bearing_to_direction(avg_bearing)
            
            unique_drivers = len(data['high_conf_fingerprints'])
            smoothed_drivers = self._get_smoothed_supply(zone_id, unique_drivers)
            
            snapshot = HourlySnapshot(
                zone_id=zone_id,
                hour=hour_ago,
                day_of_week=hour_ago.weekday(),
                unique_drivers=int(smoothed_drivers),
                total_observations=data['observations'],
                uberx_count=data['types'].get('UberX', 0),
                comfort_count=data['types'].get('Comfort', 0),
                xl_count=data['types'].get('XL', 0),
                black_count=data['types'].get('Black', 0),
                avg_bearing=avg_bearing,
                bearing_variance=bearing_variance,
                primary_direction=primary_direction,
                avg_confidence=sum(data['confidences']) / len(data['confidences']) if data['confidences'] else 0
            )
            
            self.db.add(snapshot)
        
        self.db.commit()
        return len(zone_data)
    
    def run_daily_analysis(self):
        from uber.models import HourlySnapshot, DailyPattern
        
        now = datetime.now()
        week_ago = now - timedelta(days=7)
        
        snapshots = HourlySnapshot.query.filter(
            HourlySnapshot.hour >= week_ago
        ).all()
        
        pattern_data = defaultdict(lambda: {
            'drivers': [],
            'uberx_pct': [],
            'xl_pct': [],
            'black_pct': [],
            'directions': []
        })
        
        for snap in snapshots:
            key = (snap.zone_id, snap.day_of_week, snap.hour.hour)
            data = pattern_data[key]
            data['drivers'].append(snap.unique_drivers)
            
            total = snap.unique_drivers or 1
            data['uberx_pct'].append(snap.uberx_count / total * 100)
            data['xl_pct'].append(snap.xl_count / total * 100)
            data['black_pct'].append(snap.black_count / total * 100)
            
            if snap.primary_direction:
                data['directions'].append(snap.primary_direction)
        
        for (zone_id, dow, hour), data in pattern_data.items():
            if not data['drivers']:
                continue
            
            drivers = data['drivers']
            avg_drivers = sum(drivers) / len(drivers)
            
            raw_std = math.sqrt(sum((d - avg_drivers)**2 for d in drivers) / len(drivers)) if len(drivers) > 1 else 0
            std_drivers = max(raw_std, avg_drivers * 0.15)
            
            primary_dir = None
            if data['directions']:
                dir_counts = defaultdict(int)
                for d in data['directions']:
                    dir_counts[d] += 1
                primary_dir = max(dir_counts, key=dir_counts.get)
            
            existing = DailyPattern.query.filter_by(
                zone_id=zone_id, day_of_week=dow, hour_of_day=hour
            ).first()
            
            if existing:
                existing.avg_drivers = avg_drivers
                existing.std_drivers = std_drivers
                existing.min_drivers = min(drivers)
                existing.max_drivers = max(drivers)
                existing.avg_uberx_pct = sum(data['uberx_pct']) / len(data['uberx_pct'])
                existing.avg_xl_pct = sum(data['xl_pct']) / len(data['xl_pct'])
                existing.avg_black_pct = sum(data['black_pct']) / len(data['black_pct'])
                existing.primary_direction = primary_dir
                existing.sample_count = len(drivers)
                existing.confidence = min(1.0, len(drivers) / 7)
                existing.last_updated = now
            else:
                pattern = DailyPattern(
                    zone_id=zone_id,
                    day_of_week=dow,
                    hour_of_day=hour,
                    avg_drivers=avg_drivers,
                    std_drivers=std_drivers,
                    min_drivers=min(drivers),
                    max_drivers=max(drivers),
                    avg_uberx_pct=sum(data['uberx_pct']) / len(data['uberx_pct']),
                    avg_xl_pct=sum(data['xl_pct']) / len(data['xl_pct']),
                    avg_black_pct=sum(data['black_pct']) / len(data['black_pct']),
                    primary_direction=primary_dir,
                    sample_count=len(drivers),
                    confidence=min(1.0, len(drivers) / 7)
                )
                self.db.add(pattern)
        
        self.db.commit()
        return len(pattern_data)
    
    def get_expected_drivers(self, zone_id: str, target_time: Optional[datetime] = None) -> Dict:
        from uber.models import DailyPattern
        
        if target_time is None:
            target_time = datetime.now()
        
        dow = target_time.weekday()
        hour = target_time.hour
        
        pattern = DailyPattern.query.filter_by(
            zone_id=zone_id,
            day_of_week=dow,
            hour_of_day=hour
        ).first()
        
        if not pattern:
            return {
                'expected_drivers': None,
                'std_dev': None,
                'confidence': 0,
                'has_data': False
            }
        
        return {
            'expected_drivers': pattern.avg_drivers,
            'std_dev': pattern.std_drivers,
            'min_expected': pattern.min_drivers,
            'max_expected': pattern.max_drivers,
            'primary_direction': pattern.primary_direction,
            'confidence': pattern.confidence,
            'sample_count': pattern.sample_count,
            'has_data': True
        }
    
    def detect_anomaly(self, zone_id: str, current_drivers: int, 
                       target_time: Optional[datetime] = None) -> Dict:
        expected = self.get_expected_drivers(zone_id, target_time)
        
        if not expected['has_data'] or expected['confidence'] < 0.5:
            return {
                'is_anomaly': False,
                'anomaly_score': 0,
                'direction': 'unknown',
                'confidence': 0,
                'reason': 'low_confidence_data'
            }
        
        expected_val = expected['expected_drivers']
        if expected_val is None or expected_val == 0:
            expected_val = 1
        
        deviation = (current_drivers - expected_val) / max(expected_val, 1)
        
        self._recent_deviations[zone_id].append(deviation)
        if len(self._recent_deviations[zone_id]) > 5:
            self._recent_deviations[zone_id] = self._recent_deviations[zone_id][-5:]
        
        recent = self._recent_deviations[zone_id]
        confirmed_anomaly = False
        
        if len(recent) >= 2:
            exceeding = sum(1 for d in recent[-3:] if abs(d) > self.ANOMALY_DEVIATION_THRESHOLD)
            confirmed_anomaly = exceeding >= 2
        else:
            confirmed_anomaly = abs(deviation) > self.ANOMALY_DEVIATION_THRESHOLD
        
        direction = 'high' if deviation > 0 else 'low'
        
        anomaly_result = {
            'is_anomaly': confirmed_anomaly,
            'anomaly_score': round(abs(deviation), 2),
            'deviation': round(deviation, 2),
            'direction': direction if confirmed_anomaly else 'normal',
            'current_drivers': current_drivers,
            'expected_drivers': round(expected_val, 1),
            'std_dev': round(expected['std_dev'], 2) if expected['std_dev'] else 0,
            'confidence': expected['confidence'],
            'zone_id': zone_id,
            'timestamp': datetime.now().isoformat()
        }
        
        if confirmed_anomaly:
            self._anomaly_history.append(anomaly_result)
            if len(self._anomaly_history) > 100:
                self._anomaly_history = self._anomaly_history[-100:]
        
        return anomaly_result
    
    def should_recommend_movement(self, from_zone: str, to_zone: str,
                                   current_data: Dict[str, int]) -> Dict:
        from_expected = self.get_expected_drivers(from_zone)
        to_expected = self.get_expected_drivers(to_zone)
        
        min_confidence = self.CONFIDENCE_THRESHOLD
        
        if from_expected['confidence'] < min_confidence or to_expected['confidence'] < min_confidence:
            return {
                'should_move': False,
                'reason': 'insufficient_data',
                'confidence': min(from_expected['confidence'], to_expected['confidence'])
            }
        
        from_current = current_data.get(from_zone, 0)
        to_current = current_data.get(to_zone, 0)
        
        from_exp = from_expected['expected_drivers'] or 1
        to_exp = to_expected['expected_drivers'] or 1
        
        from_ratio = from_current / max(from_exp, 1)
        to_ratio = to_current / max(to_exp, 1)
        
        should_move = from_ratio > 1.25 and to_ratio < 0.8
        
        data_volume = from_expected.get('sample_count', 0) + to_expected.get('sample_count', 0)
        decision_confidence = min(1.0, data_volume / 20) * min(from_expected['confidence'], to_expected['confidence'])
        
        decision = {
            'should_move': should_move and decision_confidence >= self.CONFIDENCE_THRESHOLD,
            'from_zone': from_zone,
            'to_zone': to_zone,
            'confidence': round(decision_confidence, 3),
            'from_current': from_current,
            'from_expected': round(from_exp, 1),
            'from_ratio': round(from_ratio, 2),
            'to_current': to_current,
            'to_expected': round(to_exp, 1),
            'to_ratio': round(to_ratio, 2),
            'reason': 'recommended' if should_move else 'not_recommended',
            'timestamp': datetime.now().isoformat()
        }
        
        self._decision_log.append(decision)
        if len(self._decision_log) > 50:
            self._decision_log = self._decision_log[-50:]
        
        return decision
    
    def get_movement_suggestions(self, current_zone_counts: Dict[str, int], 
                                  top_n: int = 3) -> List[Dict]:
        suggestions = []
        
        oversupplied = []
        undersupplied = []
        
        for zone_id, count in current_zone_counts.items():
            expected = self.get_expected_drivers(zone_id)
            if not expected['has_data'] or expected['confidence'] < self.CONFIDENCE_THRESHOLD:
                continue
            
            exp_val = expected['expected_drivers'] or 1
            ratio = count / max(exp_val, 1)
            
            if ratio > 1.25:
                oversupplied.append({
                    'zone_id': zone_id,
                    'surplus': count - exp_val,
                    'ratio': ratio,
                    'confidence': expected['confidence']
                })
            elif ratio < 0.8:
                undersupplied.append({
                    'zone_id': zone_id,
                    'shortage': exp_val - count,
                    'ratio': ratio,
                    'confidence': expected['confidence']
                })
        
        for from_zone in sorted(oversupplied, key=lambda x: -x['ratio'])[:3]:
            for to_zone in sorted(undersupplied, key=lambda x: x['ratio'])[:3]:
                if from_zone['confidence'] >= self.CONFIDENCE_THRESHOLD and \
                   to_zone['confidence'] >= self.CONFIDENCE_THRESHOLD:
                    
                    priority = (from_zone['ratio'] - 1) + (1 - to_zone['ratio'])
                    priority *= min(from_zone['confidence'], to_zone['confidence'])
                    
                    suggestions.append({
                        'from_zone': from_zone['zone_id'],
                        'to_zone': to_zone['zone_id'],
                        'priority': round(priority, 2),
                        'from_ratio': round(from_zone['ratio'], 2),
                        'to_ratio': round(to_zone['ratio'], 2),
                        'surplus': round(from_zone['surplus'], 1),
                        'shortage': round(to_zone['shortage'], 1),
                        'confidence': round(min(from_zone['confidence'], to_zone['confidence']), 2)
                    })
        
        return sorted(suggestions, key=lambda x: -x['priority'])[:top_n]
    
    def learn_correlations(self):
        from uber.models import HourlySnapshot, CorrelationModel
        
        now = datetime.now()
        two_weeks_ago = now - timedelta(days=14)
        
        snapshots = HourlySnapshot.query.filter(
            HourlySnapshot.hour >= two_weeks_ago
        ).order_by(HourlySnapshot.hour).all()
        
        zone_timeseries = defaultdict(list)
        for snap in snapshots:
            zone_timeseries[snap.zone_id].append({
                'hour': snap.hour,
                'drivers': snap.unique_drivers,
                'direction': snap.primary_direction
            })
        
        zones = list(zone_timeseries.keys())
        correlations_found = 0
        
        for source_zone in zones:
            for target_zone in zones:
                if source_zone == target_zone:
                    continue
                
                for lag in [1, 2, 3, 4]:
                    series1 = zone_timeseries[source_zone]
                    series2 = zone_timeseries[target_zone]
                    
                    if len(series1) < self.MIN_SAMPLES_FOR_CORRELATION or \
                       len(series2) < self.MIN_SAMPLES_FOR_CORRELATION:
                        continue
                    
                    correlation = self._calculate_lagged_correlation(series1, series2, lag)
                    
                    if abs(correlation) >= self.MIN_CORRELATION:
                        existing = CorrelationModel.query.filter_by(
                            source_zone_id=source_zone,
                            target_zone_id=target_zone,
                            lag_hours=lag
                        ).first()
                        
                        if existing:
                            existing.correlation_strength = correlation
                            existing.sample_count += 1
                            existing.updated_at = now
                        else:
                            corr = CorrelationModel(
                                source_zone_id=source_zone,
                                target_zone_id=target_zone,
                                lag_hours=lag,
                                correlation_strength=correlation,
                                cause_pattern='high_drivers' if correlation > 0 else 'low_drivers',
                                effect_pattern='surge' if correlation > 0 else 'decline',
                                sample_count=len(series1),
                                confidence=min(1.0, abs(correlation))
                            )
                            self.db.add(corr)
                        
                        correlations_found += 1
        
        self.db.commit()
        return correlations_found
    
    def generate_predictions(self, hours_ahead: int = 4):
        from uber.models import DailyPattern, CorrelationModel, PredictionModel, HourlySnapshot
        
        now = datetime.now()
        predictions_made = 0
        
        zones = self.db.query(DailyPattern.zone_id).distinct().all()
        zones = [z[0] for z in zones]
        
        for zone_id in zones:
            pattern_check = DailyPattern.query.filter_by(zone_id=zone_id).first()
            if pattern_check and pattern_check.confidence < 0.5:
                continue
            
            for hour_offset in range(1, hours_ahead + 1):
                target_time = now + timedelta(hours=hour_offset)
                target_dow = target_time.weekday()
                target_hour = target_time.hour
                
                pattern = DailyPattern.query.filter_by(
                    zone_id=zone_id,
                    day_of_week=target_dow,
                    hour_of_day=target_hour
                ).first()
                
                if not pattern or pattern.confidence < 0.5:
                    continue
                
                predicted_drivers = pattern.avg_drivers
                confidence = pattern.confidence
                
                correlations = CorrelationModel.query.filter_by(
                    target_zone_id=zone_id,
                    lag_hours=hour_offset
                ).filter(CorrelationModel.correlation_strength >= self.MIN_CORRELATION).all()
                
                for corr in correlations:
                    recent_snap = HourlySnapshot.query.filter_by(
                        zone_id=corr.source_zone_id
                    ).order_by(HourlySnapshot.hour.desc()).first()
                    
                    if recent_snap:
                        source_pattern = DailyPattern.query.filter_by(
                            zone_id=corr.source_zone_id,
                            day_of_week=now.weekday(),
                            hour_of_day=now.hour
                        ).first()
                        
                        if source_pattern and source_pattern.avg_drivers > 0:
                            deviation = (recent_snap.unique_drivers - source_pattern.avg_drivers) / source_pattern.avg_drivers
                            adjustment = deviation * corr.correlation_strength * predicted_drivers * 0.3
                            predicted_drivers += adjustment
                            confidence = min(confidence, corr.confidence)
                
                prediction = PredictionModel(
                    zone_id=zone_id,
                    prediction_type='driver_count',
                    target_time=target_time.replace(minute=0, second=0, microsecond=0),
                    predicted_drivers=int(max(0, predicted_drivers)),
                    predicted_direction=pattern.primary_direction,
                    confidence=confidence,
                    factors_used=f"pattern,correlations:{len(correlations)}"
                )
                
                self.db.add(prediction)
                predictions_made += 1
        
        self.db.commit()
        return predictions_made
    
    def validate_predictions(self):
        from uber.models import PredictionModel, HourlySnapshot
        
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)
        
        predictions = PredictionModel.query.filter(
            PredictionModel.target_time <= hour_ago,
            PredictionModel.validated_at.is_(None)
        ).all()
        
        validated = 0
        
        for pred in predictions:
            actual = HourlySnapshot.query.filter_by(
                zone_id=pred.zone_id,
                hour=pred.target_time
            ).first()
            
            if actual:
                pred.actual_drivers = actual.unique_drivers
                
                if pred.predicted_drivers and pred.predicted_drivers > 0:
                    error = abs(pred.predicted_drivers - actual.unique_drivers) / pred.predicted_drivers
                    pred.accuracy_score = max(0, 1 - error)
                else:
                    pred.accuracy_score = 0
                
                pred.validated_at = now
                validated += 1
        
        self.db.commit()
        return validated
    
    def get_hotspots(self, top_n: int = 10) -> List[Dict]:
        from uber.models import HourlySnapshot
        
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)
        
        recent = HourlySnapshot.query.filter(
            HourlySnapshot.hour >= hour_ago
        ).order_by(HourlySnapshot.unique_drivers.desc()).limit(top_n).all()
        
        return [{
            'zone_id': s.zone_id,
            'drivers': s.unique_drivers,
            'direction': s.primary_direction,
            'uberx': s.uberx_count,
            'xl': s.xl_count,
            'black': s.black_count
        } for s in recent]
    
    def get_predictions_for_zone(self, zone_id: str) -> List[Dict]:
        from uber.models import PredictionModel
        
        now = datetime.now()
        
        predictions = PredictionModel.query.filter(
            PredictionModel.zone_id == zone_id,
            PredictionModel.target_time >= now,
            PredictionModel.validated_at.is_(None)
        ).order_by(PredictionModel.target_time).limit(8).all()
        
        return [{
            'target_time': p.target_time.isoformat(),
            'predicted_drivers': p.predicted_drivers,
            'direction': p.predicted_direction,
            'confidence': p.confidence
        } for p in predictions]
    
    def get_zone_patterns(self, zone_id: str) -> Dict:
        from uber.models import DailyPattern
        
        patterns = DailyPattern.query.filter_by(zone_id=zone_id).all()
        
        by_day = defaultdict(list)
        for p in patterns:
            by_day[p.day_of_week].append({
                'hour': p.hour_of_day,
                'avg_drivers': p.avg_drivers,
                'std_drivers': p.std_drivers,
                'direction': p.primary_direction,
                'confidence': p.confidence
            })
        
        return dict(by_day)
    
    def get_anomaly_history(self, limit: int = 20) -> List[Dict]:
        return self._anomaly_history[-limit:]
    
    def get_decision_log(self, limit: int = 20) -> List[Dict]:
        return self._decision_log[-limit:]
    
    def _bearing_to_direction(self, bearing: float) -> str:
        directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        index = int((bearing + 22.5) % 360 / 45)
        return directions[index]
    
    def _calculate_lagged_correlation(self, series1: List[Dict], series2: List[Dict], lag: int) -> float:
        if len(series1) < lag + 5 or len(series2) < lag + 5:
            return 0
        
        values1 = []
        values2 = []
        
        hour_map1 = {s['hour']: s['drivers'] for s in series1}
        hour_map2 = {s['hour']: s['drivers'] for s in series2}
        
        for s in series1:
            t1 = s['hour']
            t2 = t1 + timedelta(hours=lag)
            
            if t1 in hour_map1 and t2 in hour_map2:
                values1.append(hour_map1[t1])
                values2.append(hour_map2[t2])
        
        if len(values1) < self.MIN_SAMPLES_FOR_CORRELATION:
            return 0
        
        mean1 = sum(values1) / len(values1)
        mean2 = sum(values2) / len(values2)
        
        numerator = sum((v1 - mean1) * (v2 - mean2) for v1, v2 in zip(values1, values2))
        
        var1 = sum((v - mean1) ** 2 for v in values1)
        var2 = sum((v - mean2) ** 2 for v in values2)
        
        denominator = math.sqrt(var1 * var2) if var1 > 0 and var2 > 0 else 1
        
        return numerator / denominator if denominator > 0 else 0
