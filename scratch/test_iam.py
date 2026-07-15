import google.auth
import google.auth.transport.requests
from google.auth import impersonated_credentials
import json
import urllib.request

def test_iam_as_sa():
    # 1. Base user credentials
    base_credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    base_credentials.refresh(auth_req)

    target_sa = "sa-esmeralda-a2a-dev@esmeralda-a2a-918f.iam.gserviceaccount.com"

    # 2. Impersonate sa-esmeralda-a2a-dev
    impersonated = impersonated_credentials.Credentials(
        source_credentials=base_credentials,
        target_principal=target_sa,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    impersonated.refresh(auth_req)
    sa_token = impersonated.token
    print(f"Impersonated SA Access Token retrieved: {sa_token[:20]}...")

    # 3. Call generateIdToken using the SA's access token
    audience = "http://legacy-dms.esmeralda.internal"
    url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{target_sa}:generateIdToken"

    req_body = json.dumps({"audience": audience, "includeEmail": True}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_body,
        headers={
            "Authorization": f"Bearer {sa_token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req) as response:
            res_json = json.loads(response.read().decode("utf-8"))
            print("🎉 SUCCESS! SA generated its own OIDC ID Token:")
            print(res_json.get("token")[:40] + "...")
    except urllib.error.HTTPError as e:
        print(f"❌ HTTPError Status: {e.code}")
        print(f"❌ HTTPError Reason: {e.reason}")
        print(f"❌ HTTPError Body: {e.read().decode('utf-8')}")

if __name__ == "__main__":
    test_iam_as_sa()
