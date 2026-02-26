import os
import json
import urllib.parse
from google_auth_oauthlib.flow import InstalledAppFlow

# The required scope for Gemini API
SCOPES = ['https://www.googleapis.com/auth/cloud-platform', 'https://www.googleapis.com/auth/generative-language.retriever']

def main():
    client_secret_path = 'credentials/client_secret.json'
    token_path = 'credentials/token.json'
    
    if not os.path.exists(client_secret_path):
        print(f"Error: {client_secret_path} not found.")
        return
        
    print("Initializing OAuth Flow...")
    # Allow local insecure redirect URLs
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    
    # We use a custom redirect URI
    flow.redirect_uri = 'http://localhost:8080/'
    
    auth_url, state = flow.authorization_url(prompt='consent', access_type='offline')
    
    print("\n" + "="*80)
    print("Please go to this URL in your browser and authorize the application:")
    print(auth_url)
    print("="*80 + "\n")
    print("After authorizing, you will be redirected to an address like: http://localhost:8080/?state=...&code=...")
    print("Even if the browser says 'Unable to connect' or 'Site can't be reached', it's fine!")
    print("Just copy the full redirected LOCALHOST URL from your browser's address bar and paste it below.\n")
    
    redirect_resp = input("Paste the full redirected URL here:\n").strip()
    
    try:
        # Fetch the token using the code in the redirected URL
        flow.fetch_token(authorization_response=redirect_resp)
        creds = flow.credentials
        
        # Save credentials
        with open(token_path, 'w') as token_file:
            token_file.write(creds.to_json())
            
        print(f"\n[SUCCESS] Authentication successful! Token saved to {token_path}")
    except Exception as e:
        print(f"\n[ERROR] Failed to fetch token: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
