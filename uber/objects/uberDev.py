import requests
import time

from source.cred import cookies, headers, loc_headers
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


def refreshToken():

    json_data = {
        'request': {
            'scope': [],
            'grantType':
            'REFRESH_TOKEN',
            'clientID':
            'SCjGHreCKCVv4tDuhi7KTYA4yLZCKgK7',
            'refreshToken':
            'MA.CAESEH2aP_gFrUj8rIZ8sp-6_3MY4Z-a6AYiATEyATE4AUIkOTdmNGZkMmItZmYzZi00ZDIzLWE0NjYtZjNiZjE1NjQ3NmQxSiBTQ2pHSHJlQ0tDVnY0dER1aGk3S1RZQTR5TFpDS2dLN1IkN2JhYTkyNWMtNTQ2Mi00ODA0LTlhNzktYjIxOWVkZGMwNjYx.UbxJUHZk_v-oG0hCH9YN0ILBJ5QgpI_N_LVNXYEFvG4.g-9rmb1jxLUh3wS9p9KWeXUgV2NMN5MAH4gDupaRNy8',
        },
    }

    response = requests.post(
        'https://cn-geo1.uber.com/rt/identity/oauth2/token',
        cookies=cookies,
        headers=headers,
        json=json_data)

    return response.json()['accessToken']


def vehicleDetails():
    params = {'includeInaccessible': 'false'}

    response = requests.get('https://cn-geo1.uber.com/rt/drivers/v2/vehicles',
                            params=params,
                            cookies=cookies,
                            headers=headers)

    return response.json()['vehicles']


def appLaunch():

    global with_ride

    json_data = {
        'launchParams': {},
    }
    headers['authorization'] = 'Bearer ' + refreshToken()
    response = requests.post('https://cn-geo1.uber.com/rt/drivers/app-launch',
                             cookies=cookies,
                             headers=headers,
                             json=json_data)
    task_scopes = response.json()['driverTasks']['taskScopes']
    if len(task_scopes) == 0:
        return response.json()
    else:
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


def driverLocation(address):

    print(f'Location Moved to: {address}')
    driverTasks = appLaunch()
    lat, long = locationTracker(address)
    time_stamp = int(driverTasks['driverTasks']['meta']['lastModifiedTimeMs'])
    # try:
    while True:
        # Check for stop signal at the start of each iteration
        if config.stop_signal == 1:
            print("Stop signal detected. Breaking driverLocation loop.")
            config.stop_signal = 0  # Reset for next time
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
        headers['authorization'] = 'Bearer ' + refreshToken()
        response = requests.post(
            'https://cn-geo1.uber.com/rt/locations/v1/upload-driver-device-locations',
            cookies=cookies,
            headers=headers,
            json=json_data,
        )
        time_stamp += 4000
        print(response.json())

        time.sleep(4)
    # except:
    #     print("Location Issue!!!")
    return
