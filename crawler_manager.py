import os
import schedule
import time
from noti_list_crawler import update_list, get_board_prefix, get_seq
from noti_body_crawler import crawl_body

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

TARGET_FILE = os.path.join(CURRENT_DIR, "crawler_target.txt")
BASE_DIR = os.path.join(CURRENT_DIR, "KAU_Crawling_Data")


def get_target_urls():
    if not os.path.exists(TARGET_FILE):
        print(f"에러: {TARGET_FILE} 파일이 없습니다.")
        return []

    with open(TARGET_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]


def manage_cleaned_folders(board_dir, file_name):
    folder_idx = 1

    while True:
        target_folder = os.path.join(board_dir, f"cleaned_txt{folder_idx}")
        os.makedirs(target_folder, exist_ok=True)

        files_in_folder = len([
            name for name in os.listdir(target_folder)
            if os.path.isfile(os.path.join(target_folder, name))
        ])

        # 이미 해당 파일이 있는 폴더가 있으면 그 폴더로 반환
        if os.path.exists(os.path.join(target_folder, file_name)):
            return target_folder

        # 새 파일은 200개 미만인 폴더에 저장
        if files_in_folder < 200:
            return target_folder

        folder_idx += 1


def run_crawler_job():
    print(f"\n=======================================================")
    print(f"공지사항 크롤링 작업 시작 ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"=======================================================")

    target_urls = get_target_urls()
    os.makedirs(BASE_DIR, exist_ok=True)

    for base_url in target_urls:
        prefix = get_board_prefix(base_url)
        board_dir = os.path.join(BASE_DIR, prefix)
        os.makedirs(board_dir, exist_ok=True)

        list_file_path = os.path.join(board_dir, f"{prefix}_list.txt")
        error_file_path = os.path.join(board_dir, f"{prefix}_error_log.txt")

        # 1. 목록 수집 및 업데이트
        try:
            update_list(base_url, list_file_path)
        except Exception as e:
            print(f"[{prefix}] 목록 업데이트 중 오류 발생: {e}")
            continue

        if not os.path.exists(list_file_path):
            print(f"[{prefix}] 수집된 목록 파일이 없습니다. 링크를 찾지 못했습니다.")
            continue

        # 2. 전체 URL 읽기
        with open(list_file_path, 'r', encoding='utf-8') as f:
            all_urls = [line.strip() for line in f if line.strip()]

        # 중복 제거
        all_urls = list(dict.fromkeys(all_urls))

        # 중요:
        # completed 목록으로 제외하지 않음.
        # 수정 감지는 noti_body_crawler.py의 manifest 해시 비교가 담당함.
        to_crawl_urls = all_urls

        print(
            f"[{prefix}] 데이터베이스 스캔 결과 -> "
            f"전체: {len(all_urls)}개 | 검사 대상: {len(to_crawl_urls)}개"
        )

        # 3. 모든 URL을 crawl_body로 넘김
        for url in to_crawl_urls:
            seq = get_seq(url)
            file_name = f"{prefix}_{seq}.txt"

            try:
                target_folder = manage_cleaned_folders(board_dir, file_name)
                crawl_body(url, target_folder)

            except Exception as e:
                print(f"[{file_name}] 오류 발생 (오류 로그 기록됨): {e}")

                with open(error_file_path, 'a', encoding='utf-8') as f:
                    f.write(
                        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"{url} | ERROR: {e}\n"
                    )

    print(f"=======================================================")
    print(f"공지사항 크롤링 작업 완료 ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"=======================================================\n")


def run_kau_crawler_job():
    """kau_crawler.py의 항공대 전체 도메인 크롤러 실행"""
    print(f"\n=======================================================")
    print(f"KAU 전체 도메인 크롤링 작업 시작 ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"=======================================================")

    try:
        from kau_crawler import crawl_kau_combined

        start_url = "https://kau.ac.kr/index/main.php"
        crawl_kau_combined(start_url)

    except Exception as e:
        print(f"[kau_crawler.py] 실행 중 오류 발생: {e}")

    print(f"=======================================================")
    print(f"KAU 전체 도메인 크롤링 작업 완료 ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"=======================================================\n")


def run_college_crawler_job():
    """college_crawler.py의 단과대/전공별 크롤러 실행"""
    print(f"\n=======================================================")
    print(f"단과대 크롤링 작업 시작 ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"=======================================================")

    try:
        from college_crawler import crawl_college_domain

        start_url = "https://college.kau.ac.kr/web/index.do?siteFlag=new_major_avm"
        crawl_college_domain(start_url)

    except Exception as e:
        print(f"[college_crawler.py] 실행 중 오류 발생: {e}")

    print(f"=======================================================")
    print(f"단과대 크롤링 작업 완료 ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"=======================================================\n")


def run_all_crawler_jobs():
    """크롤러 매니저에서 관리하는 모든 크롤러를 순서대로 실행"""
    print(f"\n#######################################################")
    print(f"전체 크롤러 실행 시작 ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"#######################################################")

    run_crawler_job()
    run_kau_crawler_job()
    run_college_crawler_job()

    print(f"#######################################################")
    print(f"전체 크롤러 실행 완료 ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"#######################################################\n")


if __name__ == "__main__":
    run_all_crawler_jobs()

    schedule.every(1).hours.do(run_all_crawler_jobs)

    print("스케줄러 대기 중... (1시간마다 전체 크롤러 자동 실행, 종료하려면 Ctrl+C)")

    while True:
        schedule.run_pending()
        time.sleep(1)