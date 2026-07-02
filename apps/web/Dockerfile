FROM node:22-alpine
WORKDIR /app
COPY package.json ./
RUN npm install
COPY index.html tsconfig.json ./
COPY src ./src
CMD ["npm", "run", "dev"]
