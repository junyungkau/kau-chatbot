#!/usr/bin/env bash
set -e
cd /opt/kau-chatbot/noti_body_crawler        # 매니저가 CURRENT_DIR 기준이라 여기서 실행
source /opt/kau-chatbot/.venv/bin/activate
set -a; source /etc/kau-chatbot.env; set +a

# 크롤링 + 임베딩 (조원 함수)
python -c "from crawler_manager import run_all_crawler_jobs; run_all_crawler_jobs()"

# 인덱스 갱신됐으니 서버 재시작 (워밍업도 자동으로 다시 돎)
sudo systemctl restart kau-chatbot

