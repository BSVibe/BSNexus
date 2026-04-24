# 사용할 기본 이미지 지정 (Node.js)
FROM node:18-alpine

# 작업 디렉토리 설정
WORKDIR /app

# 의존성 파일 복사
COPY package*.json ./

# 패키지 설치
RUN npm install

# 소스 코드 복사
COPY . .

# 포트 노출
EXPOSE 80

# 컨테이너 실행 시 실행할 명령어 (예: React 개발 서버)
CMD ["npm", "start"]