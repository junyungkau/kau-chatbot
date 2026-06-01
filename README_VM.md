# VM 배포 가이드

Cloudtype Docker 이미지 용량 제한을 피하기 위해 AWS EC2 또는 Google Cloud VM에서 Python 서버를 직접 실행하는 방식입니다.

## 권장 사양

- Ubuntu 22.04 또는 24.04
- RAM 4GB 권장, 최소 2GB
- 디스크 30GB 권장
- 방화벽 인바운드: `22`, `80`, `443`, 테스트용 `8000`

## 1. 서버 접속

```bash
ssh -i your-key.pem ubuntu@SERVER_IP
```

Google Cloud VM이면 사용자명이 다를 수 있습니다.

## 2. 프로젝트 다운로드

```bash
sudo mkdir -p /opt/kau-chatbot
sudo chown "$USER":"$USER" /opt/kau-chatbot
git clone https://github.com/junyungkau/kau-chatbot.git /opt/kau-chatbot
cd /opt/kau-chatbot
```

## 3. Python 환경 설치

```bash
bash deploy/setup_ubuntu.sh
```

## 4. Gemini API Key 등록

```bash
sudo cp deploy/kau-chatbot.env.example /etc/kau-chatbot.env
sudo nano /etc/kau-chatbot.env
```

아래 값을 실제 키로 바꿉니다.

```text
GOOGLE_API_KEY=your_gemini_api_key_here
PORT=8000
```

## 5. systemd 서비스 등록

```bash
sudo cp deploy/kau-chatbot.service /etc/systemd/system/kau-chatbot.service
sudo systemctl daemon-reload
sudo systemctl enable kau-chatbot
sudo systemctl start kau-chatbot
```

상태 확인:

```bash
sudo systemctl status kau-chatbot --no-pager
journalctl -u kau-chatbot -f
```

## 6. 서버 테스트

```bash
curl http://127.0.0.1:8000/health
```

외부에서 임시 테스트:

```text
http://SERVER_IP:8000/health
```

## 7. nginx와 HTTPS

도메인이 있다면 `deploy/nginx.conf.example`의 `server_name`을 수정한 뒤 nginx에 적용합니다.

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/kau-chatbot
sudo nano /etc/nginx/sites-available/kau-chatbot
sudo ln -s /etc/nginx/sites-available/kau-chatbot /etc/nginx/sites-enabled/kau-chatbot
sudo nginx -t
sudo systemctl reload nginx
```

HTTPS는 Certbot을 사용합니다.

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example.com
```

카카오 i 오픈빌더 스킬 URL:

```text
https://your-domain.example.com/kakao
```

도메인 없이 테스트만 할 때는 임시로 아래처럼 확인할 수 있습니다.

```text
http://SERVER_IP:8000/kakao
```
