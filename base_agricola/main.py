from fastapi import FastAPI

app = FastAPI(title="TerraMind · Base agrícola")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
