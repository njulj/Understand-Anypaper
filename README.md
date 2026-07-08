# Understand Anypaper

<img width="3394" height="1940" alt="image" src="https://github.com/user-attachments/assets/e02b1854-1b46-445f-b4ce-9d4fc2c457cc" />
<img width="3394" height="1940" alt="image" src="https://github.com/user-attachments/assets/c7ba7435-a302-467b-a1d4-1e36f54c3b95" />


## Quick start

[Devbox](https://github.com/jetify-com/devbox) is the recommended local development path. It installs Node.js, uv, and
PostgreSQL 16 with pgvector, then runs the database, API server, and web server
with `devbox services`.

```bash
cp .env.example .env
devbox services up
```

Web client: <http://localhost:5173>

```bash
devbox services up --background
```
