import subprocess
import json
import os
import requests

def get_auth_token():
    """Get Replit identity token for authentication"""
    hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
    if not hostname:
        raise Exception("REPLIT_CONNECTORS_HOSTNAME not set - emails only work in deployed Replit apps")
    
    result = subprocess.run(
        ['replit', 'identity', 'create', '--audience', f'https://{hostname}'],
        capture_output=True,
        text=True
    )
    
    token = result.stdout.strip()
    if not token:
        raise Exception("Failed to get Replit identity token")
    
    return f"Bearer {token}", hostname


def send_email(to: str, subject: str, body: str, html: str = None):
    """
    Send an email using Replit's mail service.
    
    Args:
        to: Recipient email address
        subject: Email subject line
        body: Plain text email body
        html: Optional HTML body
    
    Returns:
        dict with send result or raises exception
    """
    try:
        auth_token, hostname = get_auth_token()
        
        payload = {
            "subject": subject,
            "text": body,
        }
        if html:
            payload["html"] = html
        
        response = requests.post(
            f"https://{hostname}/api/v2/mailer/send",
            headers={
                "Content-Type": "application/json",
                "Replit-Authentication": auth_token,
            },
            json=payload,
            timeout=30
        )
        
        if not response.ok:
            error_data = response.json() if response.text else {}
            raise Exception(error_data.get('message', f'Failed to send email: {response.status_code}'))
        
        return response.json()
    except subprocess.SubprocessError as e:
        raise Exception(f"Failed to get auth token: {e}")
    except requests.RequestException as e:
        raise Exception(f"Failed to send email request: {e}")
