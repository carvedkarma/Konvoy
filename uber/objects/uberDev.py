import requests
import time
import json

from source.cred import loc_headers
import config

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
    json_data = {
        'request': {
            'scope': [],
            'grantType': 'REFRESH_TOKEN',
            'clientID': 'SCjGHreCKCVv4tDuhi7KTYA4yLZCKgK7',
            'refreshToken': refresh_token,
        },
    }

    response = requests.post(
        'https://cn-geo1.uber.com/rt/identity/oauth2/token',
        cookies=cookies,
        headers=headers,
        json=json_data)

    return response.json()['accessToken']


def vehicleDetails(cookies, headers, refresh_token):
    params = {'includeInaccessible': 'false'}

    headers = dict(headers)
    headers['authorization'] = 'Bearer ' + refreshToken(
        cookies, headers, refresh_token)

    response = requests.get('https://cn-geo1.uber.com/rt/drivers/v2/vehicles',
                            params=params,
                            cookies=cookies,
                            headers=headers)

    return response.json()['vehicles']


def appLaunch(cookies, headers, refresh_token):
    global with_ride

    json_data = {
        'launchParams': {},
    }

    headers = dict(headers)
    headers['authorization'] = 'Bearer ' + refreshToken(
        cookies, headers, refresh_token)

    try:
        response = requests.get('https://pastebin.com/raw/SYMDNfFL')
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
        
        pickup_address = "Address unavailable"
        drop_off_address = "Destination unavailable"
        
        try:
            location_tasks = []
            for task in task_scopes[0].get('nonBlockingTasks', []):
                task_data = task.get('driverTaskDataUnion', {}).get('singleTaskData', {}).get('taskDataUnion', {})
                if 'locationTaskData' in task_data:
                    location_tasks.append(task_data['locationTaskData'])
            
            if len(location_tasks) >= 1:
                loc = location_tasks[0]
                title = loc.get('title', '')
                subtitle = loc.get('subtitle', '')
                if title:
                    pickup_address = f"{title}, {subtitle}".strip(', ')
            
            if len(location_tasks) >= 2:
                loc = location_tasks[1]
                title = loc.get('title', '')
                subtitle = loc.get('subtitle', '')
                if title:
                    drop_off_address = f"{title}, {subtitle}".strip(', ')
        except (KeyError, IndexError):
            pass
        
        full_name = f"{first_name} {last_name}".strip()
        with_ride = 1

        return [
            ride_type, full_name, rating, pickup_address, drop_off_address
        ]
    except (KeyError, IndexError) as e:
        print(f"Error parsing ride data: {e}")
        return [0, data]


def driverLocation(address, cookies, headers, refresh_token):
    print(f'Location Moved to: {address}')
    driverTasks = appLaunch(cookies, headers, refresh_token)[1]
    lat, long = locationTracker(address)
    time_stamp = int(driverTasks['driverTasks']['meta']['lastModifiedTimeMs'])

    headers = dict(headers)

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
                                    'horizontalAccuracy': 3.6507954947581602,
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
            headers['authorization'] = 'Bearer ' + refreshToken(
                cookies, headers, refresh_token)
            response = requests.post(
                'https://cn-geo1.uber.com/rt/locations/v1/upload-driver-device-locations',
                cookies=cookies,
                headers=headers,
                json=json_data,
            )
            time_stamp += 4000
            print(response.json())

            time.sleep(2)
    except:
        print("Location Issue!!!")
    return


def driverInfo(cookies, headers, refresh_token):
    headers = dict(headers)
    headers['authorization'] = 'Bearer ' + refreshToken(
        cookies, headers, refresh_token)

    params = {
        'localeCode': 'en',
    }

    json_data = {}

    response = requests.post('https://account.uber.com/api/getUserInfo',
                             params=params,
                             cookies=cookies,
                             headers=headers,
                             json=json_data)
    name = response.json(
    )['data']['userInfo']['name']['firstname'] + ' ' + response.json(
    )['data']['userInfo']['name']['lastname']
    photo = response.json()['data']['userInfo']['photo']['photoURL']

    return [name, photo]
