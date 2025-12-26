import requests
import time
import json
import math

from source.cred import loc_headers, fare_cookies, fare_headers
import config

with_ride = 0


def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates in kilometers using Haversine formula"""
    R = 6371  # Earth's radius in km

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = math.sin(
        delta_lat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(
            delta_lon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return round(R * c, 1)


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
        trip_distance = None
        pickup_coords = None
        dropoff_coords = None
        trip_status = "Unknown"

        try:
            trip_status = task_scopes[0]['completionTask'][
                'coalescedDataUnion']['pickupCoalescedTaskData']['info'][
                    'status']
        except (KeyError, IndexError):
            pass

        try:
            all_location_tasks = []
            for scope in task_scopes:
                for task in scope.get('nonBlockingTasks', []):
                    task_data = task.get('driverTaskDataUnion',
                                         {}).get('singleTaskData',
                                                 {}).get('taskDataUnion', {})
                    if 'locationTaskData' in task_data:
                        all_location_tasks.append(
                            task_data['locationTaskData'])

            if len(all_location_tasks) >= 1:
                loc = all_location_tasks[0]
                title = loc.get('title', '')
                subtitle = loc.get('subtitle', '')
                if title:
                    pickup_address = f"{title}, {subtitle}".strip(', ')
                pickup_coords = (loc.get('latitude'), loc.get('longitude'))

            if len(all_location_tasks) >= 2:
                loc = all_location_tasks[1]
                title = loc.get('title', '')
                subtitle = loc.get('subtitle', '')
                if title:
                    drop_off_address = f"{title}, {subtitle}".strip(', ')
                dropoff_coords = (loc.get('latitude'), loc.get('longitude'))

            json_data = {
                'operationName':
                'Products',
                'variables': {
                    'includeRecommended':
                    False,
                    'destinations': [
                        {
                            'latitude': dropoff_coords[0],
                            'longitude': dropoff_coords[1],
                        },
                    ],
                    'payment': {
                        'paymentProfileUUID':
                        '33ec509c-9a7f-57d4-ad1f-3124df7586c8',
                        'uberCashToggleOn': True,
                    },
                    'paymentProfileUUID':
                    '33ec509c-9a7f-57d4-ad1f-3124df7586c8',
                    'pickup': {
                        'latitude': pickup_coords[0],
                        'longitude': pickup_coords[1],
                    },
                },
                'query':
                'query Products($capacity: Int, $destinations: [InputCoordinate!]!, $includeRecommended: Boolean = false, $isRiderCurrentUser: Boolean, $payment: InputPayment, $paymentProfileUUID: String, $pickup: InputCoordinate!, $pickupFormattedTime: String, $profileType: String, $profileUUID: String, $voucherUUID: String, $voucherPolicyUUID: String, $returnByFormattedTime: String, $stuntID: String, $targetProductType: EnumRVWebCommonTargetProductType) {\n  products(\n    capacity: $capacity\n    destinations: $destinations\n    includeRecommended: $includeRecommended\n    isRiderCurrentUser: $isRiderCurrentUser\n    payment: $payment\n    paymentProfileUUID: $paymentProfileUUID\n    pickup: $pickup\n    pickupFormattedTime: $pickupFormattedTime\n    profileType: $profileType\n    profileUUID: $profileUUID\n    voucherUUID: $voucherUUID\n    voucherPolicyUUID: $voucherPolicyUUID\n    returnByFormattedTime: $returnByFormattedTime\n    stuntID: $stuntID\n    targetProductType: $targetProductType\n  ) {\n    ...ProductsFragment\n    __typename\n  }\n}\n\nfragment ProductsFragment on RVWebCommonProductsResponse {\n  defaultVVID\n  hourlyTiersWithMinimumFare {\n    ...HourlyTierFragment\n    __typename\n  }\n  intercity {\n    ...IntercityFragment\n    __typename\n  }\n  links {\n    iFrame\n    text\n    url\n    __typename\n  }\n  productsUnavailableMessage\n  tiers {\n    ...TierFragment\n    __typename\n  }\n  __typename\n}\n\nfragment BadgesFragment on RVWebCommonProductBadge {\n  backgroundColor\n  color\n  contentColor\n  icon\n  inactiveBackgroundColor\n  inactiveContentColor\n  text\n  __typename\n}\n\nfragment HourlyTierFragment on RVWebCommonHourlyTier {\n  description\n  distance\n  fare\n  fareAmountE5\n  farePerHour\n  minutes\n  packageVariantUUID\n  preAdjustmentValue\n  __typename\n}\n\nfragment IntercityFragment on RVWebCommonIntercityInfo {\n  oneWayIntercityConfig(destinations: $destinations, pickup: $pickup) {\n    ...IntercityConfigFragment\n    __typename\n  }\n  roundTripIntercityConfig(destinations: $destinations, pickup: $pickup) {\n    ...IntercityConfigFragment\n    __typename\n  }\n  __typename\n}\n\nfragment IntercityConfigFragment on RVWebCommonIntercityConfig {\n  description\n  onDemandAllowed\n  reservePickup {\n    ...IntercityTimePickerFragment\n    __typename\n  }\n  returnBy {\n    ...IntercityTimePickerFragment\n    __typename\n  }\n  __typename\n}\n\nfragment IntercityTimePickerFragment on RVWebCommonIntercityTimePicker {\n  bookingRange {\n    maximum\n    minimum\n    __typename\n  }\n  header {\n    subTitle\n    title\n    __typename\n  }\n  __typename\n}\n\nfragment TierFragment on RVWebCommonProductTier {\n  products {\n    ...ProductFragment\n    __typename\n  }\n  title\n  __typename\n}\n\nfragment ProductFragment on RVWebCommonProduct {\n  badges {\n    ...BadgesFragment\n    __typename\n  }\n  cityID\n  currencyCode\n  description\n  detailedDescription\n  discountPrimary\n  displayName\n  estimatedTripTime\n  etaStringShort\n  fares {\n    capacity\n    discountPrimary\n    fare\n    fareAmountE5\n    hasPromo\n    hasRidePass\n    meta\n    preAdjustmentValue\n    __typename\n  }\n  hasPromo\n  hasRidePass\n  hasBenefitsOnFare\n  hourly {\n    tiers {\n      ...HourlyTierFragment\n      __typename\n    }\n    overageRates {\n      ...HourlyOverageRatesFragment\n      __typename\n    }\n    __typename\n  }\n  iconType\n  id\n  is3p\n  isAvailable\n  legalConsent {\n    ...ProductLegalConsentFragment\n    __typename\n  }\n  parentProductUuid\n  preAdjustmentValue\n  productImageUrl\n  productUuid\n  reserveEnabled\n  __typename\n}\n\nfragment ProductLegalConsentFragment on RVWebCommonProductLegalConsent {\n  header\n  image {\n    url\n    width\n    __typename\n  }\n  description\n  enabled\n  ctaUrl\n  ctaDisplayString\n  buttonLabel\n  showOnce\n  shouldBlockRequest\n  __typename\n}\n\nfragment HourlyOverageRatesFragment on RVWebCommonHourlyOverageRates {\n  perDistanceUnit\n  perTemporalUnit\n  __typename\n}\n',
            }

            response = requests.post('https://m.uber.com/go/graphql',
                                     cookies=fare_cookies,
                                     headers=fare_headers,
                                     json=json_data)

            if pickup_coords and dropoff_coords and all(pickup_coords) and all(
                    dropoff_coords):
                trip_distance = calculate_distance(pickup_coords[0],
                                                   pickup_coords[1],
                                                   dropoff_coords[0],
                                                   dropoff_coords[1])
        except (KeyError, IndexError):
            pass

        fare_price = None
        eta_minutes = None
        fare_distance = None
        ride_type_image = None

        ride_type_images = {
            'uberx':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/UberX_v1.png',
            'uberxl':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/UberXL_v1.png',
            'uber comfort':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/UberComfort_v1.png',
            'uber black':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/Black_v1.png',
            'uber green':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/Green_v1.png',
            'uber pet':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/UberX_v1.png',
        }

        ride_type_lower = ride_type.lower()
        for key, img_url in ride_type_images.items():
            if key in ride_type_lower or ride_type_lower in key:
                ride_type_image = img_url
                break
        if not ride_type_image:
            ride_type_image = ride_type_images.get('uberx')

        try:
            if response and response.status_code == 200:
                products_data = response.json()
                tiers = products_data.get('data', {}).get('products',
                                                          {}).get('tiers', [])

                for tier in tiers:
                    for product in tier.get('products', []):
                        product_name = product.get('displayName', '').lower()
                        if ride_type.lower() in product_name or product_name in ride_type.lower():
                            fares_list = product.get('fares', [])
                            if fares_list:
                                fare_price = fares_list[0].get('fare')
                            estimated_seconds = product.get('estimatedTripTime')
                            if estimated_seconds:
                                eta_minutes = round(estimated_seconds / 60)
                            if product.get('productImageUrl'):
                                ride_type_image = product.get('productImageUrl')
                            break
                    if fare_price:
                        break

                if not fare_price and tiers:
                    first_product = tiers[0].get('products', [{}])[0] if tiers[0].get('products') else {}
                    fares_list = first_product.get('fares', [])
                    if fares_list:
                        fare_price = fares_list[0].get('fare')
                    estimated_seconds = first_product.get('estimatedTripTime')
                    if estimated_seconds:
                        eta_minutes = round(estimated_seconds / 60)
                    if first_product.get('productImageUrl'):
                        ride_type_image = first_product.get('productImageUrl')
        except Exception as e:
            print(f"Error parsing product pricing: {e}")

        full_name = f"{first_name} {last_name}".strip()
        with_ride = 1

        return {
            'ride_type': ride_type,
            'full_name': full_name,
            'rating': rating,
            'pickup_address': pickup_address,
            'drop_off_address': drop_off_address,
            'trip_distance': trip_distance,
            'trip_status': trip_status,
            'pickup_coords': pickup_coords,
            'dropoff_coords': dropoff_coords,
            'fare_price': fare_price,
            'eta_minutes': eta_minutes,
            'fare_distance': fare_distance,
            'ride_type_image': ride_type_image
        }
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
