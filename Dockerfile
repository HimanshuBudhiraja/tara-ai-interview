# One image, both frontends, one API process.
#
# The two apps build separately and are served from the same origin, so the
# candidate never has a cross-origin hop and the recruiter console never needs
# CORS in production.

FROM node:20-slim AS web
WORKDIR /build
COPY package.json ./
COPY apps/candidate/package.json apps/candidate/
COPY apps/recruiter/package.json apps/recruiter/
COPY packages/ui/package.json packages/ui/
RUN npm install
COPY . .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/ services/
COPY packages/ packages/
COPY tests/ tests/
COPY tools/ tools/
COPY content/ content/
COPY --from=web /build/apps/candidate/dist apps/candidate/dist
COPY --from=web /build/apps/recruiter/dist apps/recruiter/dist
EXPOSE 8000
CMD ["uvicorn", "services.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
