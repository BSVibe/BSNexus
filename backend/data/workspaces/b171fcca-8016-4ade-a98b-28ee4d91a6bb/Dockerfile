# frontend/Dockerfile
# 1단계: 빌드 단계
FROM node:18-alpine as builder

WORKDIR /app

# 의존성 설치
COPY package*.json ./
RUN npm install

# 소스 코드 복사 및 빌드
COPY . .
RUN npm run build

# 2단계: 실행 단계
FROM nginx:alpine

# 빌드된 파일을 Nginx가 사용할 디렉토리에 복사
COPY --from=builder /app/build /usr/share/nginx/html

# Nginx 구성 (기본값 사용)
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]