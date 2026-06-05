# Google Authentication

This project uses two Google authentication mechanisms.

## Vertex AI Gemini

Gemini report selection and Gemini Search Grounding run through Vertex AI with a service account:

```text
secrets/google-service-account.json
```

Docker exposes it as:

```env
GOOGLE_APPLICATION_CREDENTIALS=/secrets/google-service-account.json
GOOGLE_CLOUD_PROJECT=your-google-cloud-project-id
GOOGLE_CLOUD_LOCATION=global
```

The service account needs permission to call Vertex AI generative models. A practical starting point is a role such as Vertex AI User on the target project.

Enable these APIs in the Google Cloud project:

```text
Vertex AI API
```

Grounding uses Google Search through the Gemini model call. No separate OAuth flow is required beyond the Vertex AI service account credentials.

## Google Drive My Drive Sync

Personal My Drive sync uses OAuth, not the service account.

Create an OAuth client:

```text
Application type: Desktop app
Download JSON as: secrets/google-drive-oauth-client.json
```

Enable this API:

```text
Google Drive API
```

The requested scope is:

```text
https://www.googleapis.com/auth/drive.file
```

This lets the app create and update files it owns in Drive. It avoids the broad `drive` scope.

Run OAuth once:

```bash
docker compose run --rm -p 8080:8080 ai-researcher \
  python3 -m ai_researcher.cli drive-auth --port 8080
```

The token is stored at:

```text
secrets/google-drive-token.json
```

## Important OAuth Setting

If the OAuth consent screen is left in Testing status, Google can issue refresh tokens that expire in 7 days for Drive scopes. For always-on operation, move the OAuth app to In production after confirming the consent screen settings.

If the token is revoked, expired, or invalid, rerun `drive-auth`.
