import json
import boto3
from botocore.exceptions import ClientError
import requests
import jinja2
import os
import zipfile
import tempfile
import uuid
import hmac
import hashlib

ENV = "dev"

# AWS clients - Initialize once
SSM_CLIENT = boto3.client('ssm')
DYNAMO_CLIENT = boto3.resource('dynamodb')
S3_CLIENT = boto3.client('s3')
SES_CLIENT = boto3.client('ses')

# Constants
S3_EXP = 3600  # S3 presigned URL expiration in seconds


def get_secret_from_ssm(parameter_name, with_decryption=True):
    """
    Retrieve a secret value from AWS Systems Manager Parameter Store.

    Args:
        parameter_name (str): The name of the parameter you want to retrieve.
        with_decryption (bool): Whether to decrypt the parameter value (for SecureString types).

    Returns:
        str: The value of the parameter if successful, None otherwise.
    """
    # Initialize the SSM client
    

    try:
        # Get the parameter
        response = SSM_CLIENT.get_parameter(
            Name=parameter_name,
            WithDecryption=with_decryption
        )
        # Return the parameter value
        return response['Parameter']['Value']
    except ClientError as e:
        print(f"Failed to retrieve parameter {parameter_name}: {e}")
        return None

def generate_hmac_signature(secret_key, message):
    """
    Generate HMAC SHA256 signature for request validation.

    Args:
        secret_key (str): The secret key for HMAC generation
        message (str): The message to sign

    Returns:
        str: Base64 encoded HMAC signature
    """
    message_bytes = message.encode('utf-8') if isinstance(message, str) else message
    secret_key_bytes = secret_key.encode('utf-8') if isinstance(secret_key, str) else secret_key
    signature = hmac.new(secret_key_bytes, message_bytes, hashlib.sha256).digest()
    import base64
    signature_base64 = base64.b64encode(signature).decode('utf-8')
    return signature_base64

def validate_request(event):
    """
    Validate incoming request using HMAC signature.

    Args:
        event: Lambda event object

    Returns:
        dict: Response with status code and message
    """
    try:
        received_signature = event.get('headers', {}).get('X-Signature')
        if not received_signature:
            return {
                'statusCode': 401,
                'body': json.dumps({'error': 'Missing X-Signature header'})
            }

        request_content = event.get('body', '')

        # Retrieve secret key from SSM Parameter Store
        secret_key = get_secret_from_ssm(f'/album-manager/{ENV}/hmac_key')
        if not secret_key:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Failed to retrieve HMAC key'})
            }

        generated_signature = generate_hmac_signature(secret_key, request_content)

        # Verify if the received signature matches the generated one
        if hmac.compare_digest(received_signature, generated_signature):
            return True
        else:
            return {
                'statusCode': 403,
                'body': json.dumps({'error': 'Authentication failed'})
            }
    except Exception as e:
        print(f"Error in validate_request: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }



# Initialize the Jinja2 environment
templateLoader = jinja2.FileSystemLoader(searchpath="./email_templates")
templateEnv = jinja2.Environment(loader=templateLoader)

import requests
import json
import base64
from requests.auth import HTTPBasicAuth

def verify_paypal_webhook(event, headers):
    """
    Verify PayPal webhook signature to ensure authenticity.

    Args:
        event: Webhook event data
        headers: HTTP headers from the webhook request

    Returns:
        bool: True if verification succeeds, False otherwise
    """
    try:
        # PayPal API endpoint for webhook verification
        verify_url = 'https://api.paypal.com/v1/notifications/verify-webhook-signature'

        # Headers from the incoming webhook event
        transmission_id = headers.get('PAYPAL-TRANSMISSION-ID')
        transmission_time = headers.get('PAYPAL-TRANSMISSION-TIME')
        cert_url = headers.get('PAYPAL-CERT-URL')
        actual_signature = headers.get('PAYPAL-TRANSMISSION-SIG')

        # Retrieve PayPal credentials from SSM
        paypal_client_id = get_secret_from_ssm(f"/album-manager/{ENV}/paypal_client_id")
        paypal_client_secret = get_secret_from_ssm(f"/album-manager/{ENV}/paypal_client_secret")
        webhook_id = get_secret_from_ssm(f"/album-manager/{ENV}/paypal_webhook_id")

        if not all([paypal_client_id, paypal_client_secret, webhook_id]):
            print("Failed to retrieve PayPal credentials from SSM")
            return False

        # Verification payload
        verification_payload = {
            'auth_algo': headers.get('PAYPAL-AUTH-ALGO'),
            'cert_url': cert_url,
            'transmission_id': transmission_id,
            'transmission_sig': actual_signature,
            'transmission_time': transmission_time,
            'webhook_id': webhook_id,
            'webhook_event': event
        }

        # Encode client ID and secret for basic auth
        auth = HTTPBasicAuth(paypal_client_id, paypal_client_secret)

        # Send verification request to PayPal with timeout
        response = requests.post(verify_url, json=verification_payload, auth=auth, timeout=10)

        # Check the verification status
        if response.status_code == 200:
            verification_status = response.json().get('verification_status')
            return verification_status == 'SUCCESS'
        else:
            print(f"Failed to verify webhook signature: {response.text}")
            return False

    except requests.exceptions.Timeout:
        print("PayPal webhook verification timed out")
        return False
    except Exception as e:
        print(f"Error verifying PayPal webhook: {str(e)}")
        return False


def process_paypal_order(event):
    # Parse the webhook event
    webhook_data = json.loads(event['body'])
    
    # Verify the webhook data with PayPal
    if verify_paypal_webhook(webhook_data['id']):
        # Extract necessary information
        sale_id = webhook_data['resource']['id']
        amount = webhook_data['resource']['amount']['total']
        currency = webhook_data['resource']['amount']['currency']
        state = webhook_data['resource']['state']
        custom_field = webhook_data['resource']['custom']
        
        # Process the order, e.g., update order status in your database, send email confirmation, etc.
        if state == 'completed':
            # Implement your order processing logic here
            print(f"Order {sale_id} completed for {amount} {currency}")
    else:
        # Handle verification failure
        print("Failed to verify PayPal webhook")

# moved to other func
# def verify_paypal_webhook(event_id):
#     # Make a request to PayPal API to verify the webhook event
#     # This is a simplified example; you'll need to set up your PayPal API credentials and use the correct endpoint
#     response = requests.get(f'https://api.paypal.com/v1/notifications/webhooks-events/{event_id}/verify',
#                             auth=(PP_CLIENT_ID, PP_CLIENT_SECRET))
#     return response.status_code == 200

# # Mock event data for testing
# mock_event = {
#     read from paypal_example.json
# }

# process_paypal_order(mock_event)

def create_client(event, context):
    table = DYNAMO_CLIENT.Table('Clients')
    body = json.loads(event['body'])
    
    client_id = str(uuid.uuid4())  # Generate a unique clientID
    client_name = body['clientName']
    email = body['email']
    # Add other fields as necessary

    response = table.put_item(
        Item={
            'clientID': client_id,
            'clientName': client_name,
            'email': email,
            # Add other fields as necessary
        }
    )

    return {
        'statusCode': 200,
        'body': json.dumps({'clientID': client_id, 'message': 'Client created successfully'})
    }


def zip_handler(event, context):
    """
    Handle album zipping and distribution.

    Args:
        event: Lambda event object
        context: Lambda context object

    Returns:
        dict: HTTP response with status code and body
    """
    try:
        # Validate request signature
        validation_result = validate_request(event)
        if validation_result is not True:
            return validation_result

        # Parse request body
        body = json.loads(event.get('body', '{}'))
        client_name = body.get('client_name')
        album_name = body.get('album_name')
        email = body.get('email')

        # Validate required fields
        if not all([client_name, album_name, email]):
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing required fields: client_name, album_name, email'})
            }

        # Sanitize inputs to prevent path traversal
        client_name = client_name.replace('/', '_').replace('..', '_')
        album_name = album_name.replace('/', '_').replace('..', '_')

        # Define album directory and zip file locations
        album_dir = f'clients/{client_name}/{album_name}/'
        zip_file_key = f'zipped-albums/{client_name}/{album_name}.zip'

        # Retrieve bucket name from environment or SSM
        bucket_name = os.environ.get('S3_BUCKET_NAME') or get_secret_from_ssm(f"/album-manager/{ENV}/s3_bucket_name")
        if not bucket_name:
            raise ValueError("Missing S3_BUCKET_NAME configuration")

        # Step 1: Zip the album
        zip_album(album_dir, zip_file_key)

        # Step 2: Generate presigned URL for download
        presigned_url = generate_presigned_url(bucket_name, zip_file_key)
        if not presigned_url:
            raise ValueError("Failed to generate presigned URL")

        # Step 3: Store details in DynamoDB
        store_album_details_in_dynamodb(client_name, album_name, zip_file_key, email, presigned_url)

        # Step 4: Send an email with the download link
        send_email_with_download_link(email, zip_file_key)

        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Album processed successfully', 'download_link': presigned_url})
        }

    except Exception as e:
        print(f"Error in zip_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }

def zip_album(album_dir, zip_file_key):
    # Use tempfile and zipfile to create a zip and upload to S3
    # This is a simplified placeholder - actual implementation will vary
    pass

def store_album_details_in_dynamodb(client_name, album_name, zip_file_key, email, download_link):
    """
    Store album details in DynamoDB.

    Args:
        client_name (str): Client name
        album_name (str): Album name
        zip_file_key (str): S3 key for the zip file
        email (str): Client email
        download_link (str): Presigned URL for download

    Returns:
        dict: DynamoDB response
    """
    try:
        table = DYNAMO_CLIENT.Table('AlbumDetails')
        import time
        response = table.put_item(
            Item={
                'albumID': str(uuid.uuid4()),  # Unique album ID
                'clientName': client_name,
                'albumName': album_name,
                'zipFileKey': zip_file_key,
                'email': email,
                'downloadLink': download_link,
                'createdAt': int(time.time()),
                'expiresAt': int(time.time()) + S3_EXP
            }
        )
        return response
    except Exception as e:
        print(f"Error storing album details in DynamoDB: {str(e)}")
        raise

def send_email_with_download_link(email, zip_file_key):
    """
    Send email with presigned download link.

    Args:
        email (str): Recipient email address
        zip_file_key (str): S3 key for the zip file

    Returns:
        None
    """
    try:
        # Retrieve configuration from environment or SSM
        bucket_name = os.environ.get('S3_BUCKET_NAME') or get_secret_from_ssm(f"/album-manager/{ENV}/s3_bucket_name")
        sender_email = os.environ.get('SES_SENDER_EMAIL') or get_secret_from_ssm(f"/album-manager/{ENV}/ses_sender_email")

        if not bucket_name or not sender_email:
            raise ValueError("Missing required configuration: S3_BUCKET_NAME or SES_SENDER_EMAIL")

        # Validate email format
        if not email or '@' not in email:
            raise ValueError(f"Invalid email address: {email}")

        # Generate a presigned URL for the zip file
        presigned_url = S3_CLIENT.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': zip_file_key},
            ExpiresIn=S3_EXP
        )

        # Send an email using SES
        SES_CLIENT.send_email(
            Source=sender_email,
            Destination={'ToAddresses': [email]},
            Message={
                'Subject': {'Data': 'Your Album is Ready for Download'},
                'Body': {
                    'Text': {'Data': f'Hi,\n\nYour album is ready for download. You can download it here:\n\n{presigned_url}\n\nThis link will expire in {S3_EXP // 3600} hour(s).\n\nThank you!'},
                    'Html': {'Data': f'<html><body><h2>Your Album is Ready!</h2><p>Your album is ready for download.</p><p><a href="{presigned_url}">Click here to download</a></p><p><em>This link will expire in {S3_EXP // 3600} hour(s).</em></p></body></html>'}
                }
            }
        )
        print(f"Email sent successfully to {email}")
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        raise

# TODO: Ensure we have the necessary permissions in your IAM role for S3, DynamoDB, and SES operations.

def send_email_with_template(recipient, fullname, links):
    """
    Send templated email with photo links.

    Args:
        recipient (str): Recipient email address
        fullname (str): Recipient full name
        links (list): List of photo download links

    Returns:
        dict: SES response or None on failure
    """
    subject = "Your photos are ready"
    charset = "UTF-8"

    try:
        # Validate email format
        if not recipient or '@' not in recipient:
            raise ValueError(f"Invalid recipient email address: {recipient}")

        # Retrieve sender email from environment or SSM
        sender_email = os.environ.get('SES_SENDER_EMAIL') or get_secret_from_ssm(f"/album-manager/{ENV}/ses_sender_email")
        if not sender_email:
            raise ValueError("Missing SES_SENDER_EMAIL configuration")

        # Load the template from the Jinja2 environment
        template = templateEnv.get_template("photo_template.html")

        # Render the template with the provided data
        body_html = template.render(fullname=fullname, links=links)

        response = SES_CLIENT.send_email(
            Destination={'ToAddresses': [recipient]},
            Message={
                'Body': {
                    'Html': {
                        'Charset': charset,
                        'Data': body_html,
                    },
                },
                'Subject': {
                    'Charset': charset,
                    'Data': subject,
                },
            },
            Source=sender_email
        )
        print(f"Template email sent successfully to {recipient}")
        return response
    except Exception as e:
        print(f"Error sending template email to {recipient}: {str(e)}")
        return None


def webhook_handler(event, context):
    """
    Handle incoming PayPal webhook events.

    Args:
        event: Lambda event object
        context: Lambda context object

    Returns:
        dict: HTTP response with status code and body
    """
    try:
        # Parse the incoming webhook data
        webhook_data = json.loads(event.get('body', '{}'))
        headers = event.get('headers', {})

        # Verify the webhook data with PayPal
        if not verify_paypal_webhook(webhook_data, headers):
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Webhook verification failed'})
            }

        # Extract purchase details from verified webhook
        resource = webhook_data.get('resource', {})
        photo_id = resource.get('custom_id')

        # Extract customer email from the payer info
        payer = resource.get('payer', {})
        customer_email = payer.get('email_address')

        if not customer_email or not photo_id:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing required fields in webhook data'})
            }

        # Retrieve bucket name from environment or SSM
        bucket_name = os.environ.get('S3_BUCKET_NAME') or get_secret_from_ssm(f"/album-manager/{ENV}/s3_bucket_name")
        if not bucket_name:
            raise ValueError("Missing S3_BUCKET_NAME configuration")

        # Generate a presigned URL for the photo
        presigned_url = generate_presigned_url(bucket_name, photo_id)

        if not presigned_url:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Failed to generate download link'})
            }

        # Send the photo link to the customer via email
        send_email(customer_email, presigned_url)

        # Store webhook event in DynamoDB for audit trail
        store_webhook_event(webhook_data)

        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Webhook processed successfully'})
        }

    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }


def generate_presigned_url(bucket_name, object_name, expiration=S3_EXP):
    """
    Generate a presigned URL for S3 object download.

    Args:
        bucket_name (str): S3 bucket name
        object_name (str): S3 object key
        expiration (int): URL expiration time in seconds

    Returns:
        str: Presigned URL or None on failure
    """
    try:
        # Validate inputs
        if not bucket_name or not object_name:
            raise ValueError("bucket_name and object_name are required")

        response = S3_CLIENT.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': object_name},
            ExpiresIn=expiration
        )
        return response
    except Exception as e:
        print(f"Error generating presigned URL: {str(e)}")
        return None

def send_email(to_email, presigned_url):
    """
    Send email with download link to customer.

    Args:
        to_email (str): Recipient email address
        presigned_url (str): Presigned download URL

    Returns:
        dict: SES response
    """
    try:
        # Validate email
        if not to_email or '@' not in to_email:
            raise ValueError(f"Invalid email address: {to_email}")

        # Retrieve sender email from environment or SSM
        sender_email = os.environ.get('SES_SENDER_EMAIL') or get_secret_from_ssm(f"/album-manager/{ENV}/ses_sender_email")
        if not sender_email:
            raise ValueError("Missing SES_SENDER_EMAIL configuration")

        response = SES_CLIENT.send_email(
            Source=sender_email,
            Destination={'ToAddresses': [to_email]},
            Message={
                'Subject': {'Data': 'Your Photo is Ready for Download'},
                'Body': {
                    'Text': {'Data': f'Hi,\n\nYour photo is ready for download:\n\n{presigned_url}\n\nThis link will expire in {S3_EXP // 3600} hour(s).\n\nThank you!'},
                    'Html': {'Data': f'<html><body><h2>Your Photo is Ready!</h2><p><a href="{presigned_url}">Click here to download your photo</a></p><p><em>This link will expire in {S3_EXP // 3600} hour(s).</em></p></body></html>'}
                }
            }
        )
        print(f"Email sent successfully to {to_email}")
        return response
    except Exception as e:
        print(f"Error sending email to {to_email}: {str(e)}")
        raise

def store_webhook_event(webhook_data):
    """
    Store webhook event in DynamoDB for audit trail.

    Args:
        webhook_data (dict): Webhook event data

    Returns:
        dict: DynamoDB response
    """
    try:
        table = DYNAMO_CLIENT.Table('PayPalWebhooks')
        import time
        response = table.put_item(
            Item={
                'order_id': webhook_data.get('id', str(uuid.uuid4())),
                'event_type': webhook_data.get('event_type'),
                'timestamp': int(time.time()),
                'data': json.dumps(webhook_data)
            }
        )
        return response
    except Exception as e:
        print(f"Error storing webhook event: {str(e)}")
        # Don't fail the webhook processing if audit logging fails
        return None
