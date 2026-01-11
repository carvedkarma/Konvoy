"""
Perth Coordinate Grid System
Covers 500+ coordinates across Perth metro with dense/sparse zones
"""

import math
from dataclasses import dataclass
from typing import List, Dict, Tuple


@dataclass
class GridPoint:
    lat: float
    lng: float
    zone_id: str
    zone_name: str
    is_dense: bool
    priority: int


class PerthGrid:
    PERTH_ZONES = [
        {'name': 'Perth CBD', 'lat': -31.9505, 'lng': 115.8605, 'radius': 3, 'dense': True, 'priority': 10},
        {'name': 'Northbridge', 'lat': -31.9440, 'lng': 115.8575, 'radius': 1.5, 'dense': True, 'priority': 9},
        {'name': 'Perth Airport', 'lat': -31.9403, 'lng': 115.9670, 'radius': 3, 'dense': True, 'priority': 10},
        {'name': 'Fremantle', 'lat': -32.0569, 'lng': 115.7439, 'radius': 2.5, 'dense': True, 'priority': 8},
        {'name': 'Subiaco', 'lat': -31.9490, 'lng': 115.8270, 'radius': 2, 'dense': True, 'priority': 7},
        {'name': 'Leederville', 'lat': -31.9360, 'lng': 115.8410, 'radius': 1.5, 'dense': True, 'priority': 7},
        {'name': 'Victoria Park', 'lat': -31.9760, 'lng': 115.8970, 'radius': 2, 'dense': False, 'priority': 6},
        {'name': 'South Perth', 'lat': -31.9720, 'lng': 115.8650, 'radius': 2, 'dense': False, 'priority': 6},
        {'name': 'Claremont', 'lat': -31.9810, 'lng': 115.7810, 'radius': 2, 'dense': False, 'priority': 5},
        {'name': 'Cottesloe', 'lat': -31.9950, 'lng': 115.7550, 'radius': 2, 'dense': False, 'priority': 5},
        {'name': 'Scarborough', 'lat': -31.8940, 'lng': 115.7610, 'radius': 2.5, 'dense': False, 'priority': 5},
        {'name': 'Mount Lawley', 'lat': -31.9290, 'lng': 115.8720, 'radius': 1.5, 'dense': False, 'priority': 6},
        {'name': 'Burswood', 'lat': -31.9590, 'lng': 115.8920, 'radius': 2, 'dense': True, 'priority': 8},
        {'name': 'Optus Stadium', 'lat': -31.9510, 'lng': 115.8890, 'radius': 1, 'dense': True, 'priority': 9},
        {'name': 'Cannington', 'lat': -32.0180, 'lng': 115.9350, 'radius': 2.5, 'dense': False, 'priority': 5},
        {'name': 'Carousel', 'lat': -32.0240, 'lng': 115.9180, 'radius': 1.5, 'dense': False, 'priority': 5},
        {'name': 'Belmont', 'lat': -31.9560, 'lng': 115.9380, 'radius': 2, 'dense': False, 'priority': 5},
        {'name': 'Midland', 'lat': -31.8890, 'lng': 116.0050, 'radius': 2.5, 'dense': False, 'priority': 5},
        {'name': 'Joondalup', 'lat': -31.7470, 'lng': 115.7680, 'radius': 3, 'dense': False, 'priority': 5},
        {'name': 'Karrinyup', 'lat': -31.8720, 'lng': 115.7780, 'radius': 2, 'dense': False, 'priority': 4},
        {'name': 'Morley', 'lat': -31.8980, 'lng': 115.9050, 'radius': 2.5, 'dense': False, 'priority': 5},
        {'name': 'Stirling', 'lat': -31.8730, 'lng': 115.8320, 'radius': 2, 'dense': False, 'priority': 4},
        {'name': 'Innaloo', 'lat': -31.8930, 'lng': 115.7980, 'radius': 2, 'dense': False, 'priority': 4},
        {'name': 'Osborne Park', 'lat': -31.9000, 'lng': 115.8110, 'radius': 2, 'dense': False, 'priority': 4},
        {'name': 'Rockingham', 'lat': -32.2820, 'lng': 115.7300, 'radius': 3, 'dense': False, 'priority': 4},
        {'name': 'Mandurah', 'lat': -32.5270, 'lng': 115.7440, 'radius': 3, 'dense': False, 'priority': 4},
        {'name': 'Armadale', 'lat': -32.1530, 'lng': 116.0150, 'radius': 2.5, 'dense': False, 'priority': 4},
        {'name': 'Thornlie', 'lat': -32.0590, 'lng': 115.9540, 'radius': 2, 'dense': False, 'priority': 4},
        {'name': 'Canning Vale', 'lat': -32.0740, 'lng': 115.9180, 'radius': 2, 'dense': False, 'priority': 4},
        {'name': 'Ellenbrook', 'lat': -31.7670, 'lng': 116.0190, 'radius': 2.5, 'dense': False, 'priority': 3},
        {'name': 'Wanneroo', 'lat': -31.7510, 'lng': 115.8090, 'radius': 2.5, 'dense': False, 'priority': 3},
        {'name': 'Clarkson', 'lat': -31.6850, 'lng': 115.7270, 'radius': 2.5, 'dense': False, 'priority': 3},
        {'name': 'Butler', 'lat': -31.6460, 'lng': 115.7050, 'radius': 2, 'dense': False, 'priority': 3},
        {'name': 'Hillarys', 'lat': -31.8240, 'lng': 115.7380, 'radius': 2, 'dense': False, 'priority': 4},
        {'name': 'Wembley', 'lat': -31.9310, 'lng': 115.8130, 'radius': 1.5, 'dense': False, 'priority': 5},
        {'name': 'Nedlands', 'lat': -31.9800, 'lng': 115.8050, 'radius': 2, 'dense': False, 'priority': 5},
        {'name': 'Applecross', 'lat': -32.0130, 'lng': 115.8380, 'radius': 2, 'dense': False, 'priority': 5},
        {'name': 'Como', 'lat': -32.0010, 'lng': 115.8650, 'radius': 1.5, 'dense': False, 'priority': 5},
        {'name': 'East Perth', 'lat': -31.9550, 'lng': 115.8750, 'radius': 1.5, 'dense': True, 'priority': 8},
        {'name': 'West Perth', 'lat': -31.9480, 'lng': 115.8430, 'radius': 1.5, 'dense': True, 'priority': 7},
        {'name': 'Kings Park', 'lat': -31.9630, 'lng': 115.8320, 'radius': 1.5, 'dense': False, 'priority': 6},
        {'name': 'Mount Hawthorn', 'lat': -31.9210, 'lng': 115.8350, 'radius': 1.5, 'dense': False, 'priority': 5},
        {'name': 'Highgate', 'lat': -31.9380, 'lng': 115.8680, 'radius': 1, 'dense': False, 'priority': 6},
        {'name': 'Perth Train Station', 'lat': -31.9505, 'lng': 115.8605, 'radius': 0.5, 'dense': True, 'priority': 10},
        {'name': 'Elizabeth Quay', 'lat': -31.9580, 'lng': 115.8580, 'radius': 0.5, 'dense': True, 'priority': 9},
        {'name': 'Crown Perth', 'lat': -31.9598, 'lng': 115.8888, 'radius': 1, 'dense': True, 'priority': 9},
    ]
    
    def __init__(self):
        self.grid_points: List[GridPoint] = []
        self._generate_grid()
    
    def _generate_grid(self):
        seen_coords = set()
        
        for zone in self.PERTH_ZONES:
            spacing_km = 0.5 if zone['dense'] else 1.0
            points = self._generate_zone_points(
                zone['lat'], zone['lng'], 
                zone['radius'], spacing_km,
                zone['name'], zone['dense'], zone['priority']
            )
            
            for point in points:
                coord_key = f"{point.lat:.4f},{point.lng:.4f}"
                if coord_key not in seen_coords:
                    seen_coords.add(coord_key)
                    self.grid_points.append(point)
        
        self.grid_points.sort(key=lambda p: -p.priority)
    
    def _generate_zone_points(self, center_lat: float, center_lng: float, 
                               radius_km: float, spacing_km: float,
                               zone_name: str, is_dense: bool, priority: int) -> List[GridPoint]:
        points = []
        zone_id = zone_name.lower().replace(' ', '_')
        
        lat_offset = spacing_km / 111.0
        lng_offset = spacing_km / (111.0 * math.cos(math.radians(center_lat)))
        
        steps = int(radius_km / spacing_km) + 1
        
        for lat_step in range(-steps, steps + 1):
            for lng_step in range(-steps, steps + 1):
                lat = center_lat + (lat_step * lat_offset)
                lng = center_lng + (lng_step * lng_offset)
                
                dist = self._haversine(center_lat, center_lng, lat, lng)
                if dist <= radius_km:
                    points.append(GridPoint(
                        lat=round(lat, 5),
                        lng=round(lng, 5),
                        zone_id=zone_id,
                        zone_name=zone_name,
                        is_dense=is_dense,
                        priority=priority
                    ))
        
        return points
    
    def _haversine(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        R = 6371
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lng = math.radians(lng2 - lng1)
        
        a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    def get_all_points(self) -> List[GridPoint]:
        return self.grid_points
    
    def get_points_by_zone(self, zone_id: str) -> List[GridPoint]:
        return [p for p in self.grid_points if p.zone_id == zone_id]
    
    def get_zone_for_coordinate(self, lat: float, lng: float) -> str:
        closest_zone = None
        min_dist = float('inf')
        
        for zone in self.PERTH_ZONES:
            dist = self._haversine(lat, lng, zone['lat'], zone['lng'])
            if dist < min_dist:
                min_dist = dist
                closest_zone = zone['name'].lower().replace(' ', '_')
        
        return closest_zone or 'unknown'
    
    def get_stats(self) -> Dict:
        zones = {}
        for p in self.grid_points:
            if p.zone_id not in zones:
                zones[p.zone_id] = {'count': 0, 'dense': p.is_dense, 'priority': p.priority}
            zones[p.zone_id]['count'] += 1
        
        return {
            'total_points': len(self.grid_points),
            'zones': len(zones),
            'dense_points': len([p for p in self.grid_points if p.is_dense]),
            'sparse_points': len([p for p in self.grid_points if not p.is_dense]),
            'zones_detail': zones
        }


PERTH_GRID = PerthGrid()
