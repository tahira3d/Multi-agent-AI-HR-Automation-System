# Render API and Streamlit Community Cloud deployment

This project deploys as two services:

1. Render hosts the FastAPI API, PostgreSQL database, the Redis-compatible service required by production configuration, and private resume disk.
2. Streamlit Community Cloud hosts only the `frontend/app.py` dashboard and calls the Render API server-side.

The split is required. Streamlit Community Cloud does not run the FastAPI API, PostgreSQL, or the project-local Ollama and speech-model runtime.

## 1. Choose the Streamlit URL

In [Streamlit Community Cloud](https://share.streamlit.io/), start creating an app from `tahira3d/Multi-agent-AI-HR-Automation-System`, branch `main`, with `frontend/app.py` as the entrypoint. Choose a custom subdomain and record the resulting URL. You need that exact HTTPS URL for `CORS_ORIGINS` in the Render Blueprint setup.

## 2. Create the Render resources

1. In Render, select **New > Blueprint** and select the GitHub repository.
2. Use the committed root [`render.yaml`](../render.yaml). It creates the `multi-agent-hr-api` web service, a private Postgres database, and a private Key Value service.
3. Keep all resources in the same region unless there is a specific data-residency requirement.
4. At the first Blueprint sync, supply each requested value:

   ```text
   CORS_ORIGINS=https://YOUR_STREAMLIT_APP.streamlit.app
   AZURE_OPENAI_ENDPOINT=https://YOUR_RESOURCE.openai.azure.com
   AZURE_OPENAI_API_KEY=YOUR_AZURE_OPENAI_KEY
   AZURE_OPENAI_DEPLOYMENT=YOUR_STRUCTURED_OUTPUT_DEPLOYMENT
   ```

   `JWT_SECRET_KEY` and `PII_ENCRYPTION_KEY` are generated securely by Render. Do not copy local `.env` values or API keys into Git.

5. Wait for the API health check at `https://YOUR_RENDER_SERVICE.onrender.com/health/ready` to return `{"status":"ready"}`.

The Blueprint intentionally selects paid API and database plans. Render free web services cannot attach a persistent disk and free Postgres instances expire, which makes them unsuitable for private candidate documents.

The production backend requires Azure OpenAI configuration. GroqCloud is retained by the codebase for local/development use only and is not accepted by the production configuration.

## 3. Complete the Streamlit deployment

In **Advanced settings > Secrets**, add the Render URL:

```toml
API_BASE_URL = "https://YOUR_RENDER_SERVICE.onrender.com"
STREAMLIT_BASE_URL = "https://YOUR_STREAMLIT_APP.streamlit.app"
```

Deploy the app, then test sign-in, upload, evaluation, and authorized review. Streamlit root-level secrets are exposed to the dashboard as environment variables, so `API_BASE_URL` is read by the existing frontend without committing it to the repository.

## Voice-screening limitation

Voice screening is intentionally local-only: it requires loopback Ollama plus locally installed Whisper and Piper assets. The Render service starts safely without those assets, but cloud voice-screening actions will report that the local runtime is unavailable. Do not expose a remote Ollama URL to bypass that restriction.

## Operational notes

- The Render disk is mounted at `/app/storage`, the path used by the backend container for private resume persistence.
- A disk-backed service is single-instance and has brief downtime during deployments. Do not use it as the sole backup; configure organizational backups and retention procedures before using real candidate data.
- The service has no automatic demo-user creation. Provision accounts through the documented administrator workflow after deployment.
