# P2 Public UI — Local synthetic demo

The frontend is a React/TypeScript/Vite client to the localhost FastAPI
service. See the root README for the synthetic fixture backend, local API key
and `npm ci && npm run dev` instructions. The key stays in the page session
memory. The frontend does not contact Groq. Sample questions refer to the
fictional Orion documents only.

Test with `npm run check`. This cannot establish production security or
human-reviewed factual correctness of any generated response.
