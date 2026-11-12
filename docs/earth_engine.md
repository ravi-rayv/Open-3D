# Earth Engine sign-in

Satellite building heights (and, optionally, Google footprints) are read from Google Earth Engine with **your own** Earth Engine project. It is free for non-commercial and research use.

## 1. Register a Cloud project for Earth Engine (once)

1. Open <https://console.cloud.google.com/earth-engine/configuration> and pick (or create) a project.
2. Choose **Unpaid usage → Academia & Research** (or the category that fits you) and confirm.
3. If asked, enable the **Earth Engine API**.
4. Note the **project ID**, e.g. `ee-yourname`.

## 2a. Website (Streamlit Cloud): service-account key

1. Cloud Console → **IAM & Admin → Service Accounts → Create service account** (name e.g. `ee-portal`).
2. Give it two roles: **Earth Engine Resource Viewer** and **Service Usage Consumer**.
3. Open the account → **Keys → Add key → Create new key → JSON**. A `.json` file downloads.
4. In the app sidebar choose **Upload my service-account key (JSON)** and upload it. The project ID is read from the key.
5. Click **Test connection**.

The key is used in memory for your session only and never stored. Keep the file private; delete the key in the Cloud Console if it leaks.

## 2b. Your own computer

```bash
pip install earthengine-api
earthengine authenticate
earthengine set_project ee-yourname
```

Then choose **This computer** in the app, or use the command line (`bldg3d run --project ee-yourname ...`).

## Common errors

| Message | Fix |
|---|---|
| *Project … is not registered to use Earth Engine* | Do step 1 for that project |
| *Caller does not have required permission to use project X* | The key belongs to another project, or the account lacks **Service Usage Consumer**. Use the key's own project ID. |
| *Please authorize access … earthengine authenticate* | On the website, use a key; *This computer* works only when running locally |
