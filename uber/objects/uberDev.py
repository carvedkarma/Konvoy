import requests
import time
import config

loc_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}

with_ride = 0


def locationTracker(addrs):
    params = {
        'format': 'json',
        'q': addrs,
    }

    response = requests.get('https://nominatim.openstreetmap.org/search',
                            params=params,
                            headers=loc_headers)

    return [response.json()[0]['lat'], response.json()[0]['lon']]


def refreshToken(cookies, headers, refresh_token):
    if not refresh_token:
        print("No refresh token provided")
        return None
    
    json_data = {
        'request': {
            'scope': [],
            'grantType': 'REFRESH_TOKEN',
            'clientID': 'SCjGHreCKCVv4tDuhi7KTYA4yLZCKgK7',
            'refreshToken': refresh_token,
        },
    }

    try:
        response = requests.post(
            'https://cn-geo1.uber.com/rt/identity/oauth2/token',
            cookies=cookies,
            headers=headers,
            json=json_data)
        return response.json().get('accessToken')
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return None


def vehicleDetails(cookies, headers):
    if not cookies or not headers:
        print("No Uber credentials provided")
        return []
    
    params = {'includeInaccessible': 'false'}

    try:
        response = requests.get('https://cn-geo1.uber.com/rt/drivers/v2/vehicles',
                                params=params,
                                cookies=cookies,
                                headers=headers)
        data = response.json()
        if 'vehicles' not in data:
            print(f"Vehicle API error: {data}")
            return []
        return data['vehicles']
    except Exception as e:
        print(f"Error fetching vehicles: {e}")
        return []


def driverInfo(cookies, headers):
    if not cookies or not headers:
        print("No Uber credentials provided")
        return None
    
    try:
        response = requests.get('https://cn-geo1.uber.com/rt/drivers/me',
                                cookies=cookies,
                                headers=headers)
        data = response.json()
        return data
    except Exception as e:
        print(f"Error fetching driver info: {e}")
        return None


def appLaunch(cookies, headers):
    global with_ride

    if not cookies or not headers:
        print("No Uber credentials provided")
        return [0, None]

    json_data = {
        'launchParams': {},
    }

    try:
        response = requests.post('https://cn-geo1.uber.com/rt/drivers/app-launch',
                                 cookies=cookies,
                                 headers=headers,
                                 json=json_data)
        data = response.json()
    except Exception as e:
        print(f"Error fetching app launch data: {e}")
        return [0, None]

    task_scopes = data.get('driverTasks', {}).get('taskScopes', [])
    if len(task_scopes) == 0:
        print("No Ride Found")
        return [0, data]
    
    try:
        print("Ride Found")
        ride_type = task_scopes[0]['completionTask']['coalescedDataUnion'][
            'pickupCoalescedTaskData']['product']['name']
        job_id = task_scopes[0]['nonBlockingTasks'][0]['driverTaskDataUnion'][
            'singleTaskData']['taskSourceKeyOption']['taskSourceKey'][
                'taskSourceUuid']
        first_name = task_scopes[0]['completionTask']['taskDataMap'][job_id][
            'pickupTaskData']['entity']['firstName']
        last_name = task_scopes[0]['completionTask']['taskDataMap'][job_id][
            'pickupTaskData']['entity']['lastName']
        rating = task_scopes[0]['completionTask']['taskDataMap'][job_id][
            'pickupTaskData']['entity']['rating']
        pickup_address = task_scopes[0]['nonBlockingTasks'][0][
            'driverTaskDataUnion']['singleTaskData']['taskDataUnion'][
                'locationTaskData']['anchorLocation']['fullAddress']
        drop_off_address_title = task_scopes[1]['nonBlockingTasks'][4][
            'driverTaskDataUnion']['singleTaskData']['taskDataUnion'][
                'locationTaskData']['title']
        drop_off_address_subtitle = task_scopes[1]['nonBlockingTasks'][4][
            'driverTaskDataUnion']['singleTaskData']['taskDataUnion'][
                'locationTaskData']['subtitle']
        drop_off_address = drop_off_address_title + ' ' + drop_off_address_subtitle
        with_ride = 1

        return [
            ride_type, first_name, last_name, rating, pickup_address,
            drop_off_address
        ]
    except Exception as e:
        print(f"Error parsing ride data: {e}")
        return [0, data]


def driverLocation(address, cookies, headers):
    if not cookies or not headers:
        print("No Uber credentials provided")
        return

    print(f'Location Moved to: {address}')
    result = appLaunch(cookies, headers)
    if result[1] is None:
        print("Cannot get driver tasks")
        return
    
    driverTasks = result[1]
    lat, long = locationTracker(address)
    time_stamp = int(driverTasks.get('driverTasks', {}).get('meta', {}).get('lastModifiedTimeMs', int(time.time() * 1000)))
    
    try:
        while True:
            if config.stop_signal == 1:
                print("Stop signal detected. Breaking driverLocation loop.")
                config.stop_signal = 0
                break

            if with_ride == 1:
                print("Ride in progress. Breaking driverLocation loop.")
                break
            
            json_data = {
                'data': {
                    'positions': [
                        {
                            'positionNavigationData': {
                                'location': {
                                    'allTimestamps': [
                                        {
                                            'ts': time_stamp,
                                        },
                                    ],
                                    'latitude': float(lat),
                                    'speed': -1,
                                    'course': -1,
                                    'horizontalAccuracy': 8.17965569028195,
                                    'provider': 'ios_core',
                                    'verticalAccuracy': 30,
                                    'altitude': 30.969567390469884,
                                    'bestTimestamp': {
                                        'ts': time_stamp,
                                    },
                                    'longitude': float(long),
                                },
                            },
                        },
                    ],
                },
            }
            
            response = requests.post(
                'https://cn-geo1.uber.com/rt/locations/v1/upload-driver-device-locations',
                cookies=cookies,
                headers=headers,
                json=json_data,
            )
            time_stamp += 4000
            print(response.json())

            time.sleep(4)
    except Exception as e:
        print(f"Location Issue: {e}")
    return
