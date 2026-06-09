import requests
from bs4 import BeautifulSoup
import re
import os
from urllib.parse import urljoin

# 브라우저처럼 보이게 해서 차단을 막는 헤더
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://kau.ac.kr/'
}

def get_board_prefix(url):
    match = re.search(r'/([^/]+)\.php', url)
    return match.group(1) if match else "board"

def get_seq(url):
    match = re.search(r'seq=(\d+)', url)
    return int(match.group(1)) if match else 0

def find_last_page_and_urls(base_url):
    page = 1
    last_count = -1
    all_urls = []
    
    while True:
        if 'page=' in base_url:
            target_url = re.sub(r'page=\d+', f'page={page}', base_url)
        else:
            connector = '&' if '?' in base_url else '?'
            target_url = f"{base_url}{connector}page={page}"

        try:
            response = requests.get(target_url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            # 🌟 [해결] 여기서 soup을 정의합니다.
            soup = BeautifulSoup(response.text, 'html.parser')
            
            link_elements = soup.find_all('a', href=True)
            current_page_urls = []
            
            for a in link_elements:
                href = a['href']
                if 'seq=' in href and 'mode=read' in href:
                    full_url = urljoin(base_url, href)
                    if full_url not in current_page_urls:
                        current_page_urls.append(full_url)
            
            current_count = len(current_page_urls)
            print(f"   -> {page}페이지 스캔 중... ({current_count}개 발견)")
            
            if current_count == 0 or (current_count < 20 and current_count == last_count):
                break
                
            all_urls.extend(current_page_urls)
            last_count = current_count
            page += 1
            
        except Exception as e:
            print(f"   ❌ 스캔 중 에러 발생: {e}")
            break
            
    unique_urls = list(dict.fromkeys(all_urls))
    return unique_urls[::-1]

def update_list(base_url, list_file_path):
    prefix = get_board_prefix(base_url)
    existing_urls = []
    
    if os.path.exists(list_file_path):
        with open(list_file_path, 'r', encoding='utf-8') as f:
            existing_urls = [line.strip() for line in f if line.strip()]
            
    # 🌟 [무한루프 방지] 기존에 저장된 글 번호(seq)들을 추출합니다.
    existing_seqs = set()
    for url in existing_urls:
        s = get_seq(url)
        if s > 0: existing_seqs.add(s)
            
    new_urls = []
    
    if not existing_urls:
        print(f"[{prefix}] 최초 실행: 전체 페이지 스캔 시작...")
        new_urls = find_last_page_and_urls(base_url)
    else:
        print(f"[{prefix}] 업데이트: 탐색 시작... (기존 데이터 {len(existing_urls)}개)")
        page = 1
        temp_new_urls = []
        
        while True:
            if 'page=' in base_url:
                target_url = re.sub(r'page=\d+', f'page={page}', base_url)
            else:
                connector = '&' if '?' in base_url else '?'
                target_url = f"{base_url}{connector}page={page}"

            try:
                response = requests.get(target_url, headers=HEADERS, timeout=10)
                # 🌟 [해결] 여기서 soup을 정의해야 name 'soup' is not defined 에러가 안 납니다.
                soup = BeautifulSoup(response.text, 'html.parser')
                
                link_elements = soup.find_all('a', href=True)
                page_found_any = False
                new_links_on_this_page = 0
                
                for a in link_elements:
                    href = a['href']
                    if 'seq=' in href and 'mode=read' in href:
                        page_found_any = True
                        full_url = urljoin(base_url, href)
                        current_seq = get_seq(full_url)
                        
                        # 번호 비교 방식으로 중복 체크
                        if current_seq not in existing_seqs and full_url not in temp_new_urls:
                            temp_new_urls.append(full_url)
                            new_links_on_this_page += 1
                
                print(f"   -> {page}페이지 탐색 완료 (새 글 {new_links_on_this_page}개)")

                # 새 글이 하나도 없는 지점에 도달하면 중단 (업데이트 완료)
                if not page_found_any or new_links_on_this_page == 0 or page > 300:
                    break
                page += 1
            except Exception as e:
                print(f"   ❌ 업데이트 중 오류 발생: {e}")
                break
        
        new_urls = temp_new_urls[::-1]

    if new_urls:
        with open(list_file_path, 'a', encoding='utf-8') as f:
            for url in new_urls:
                f.write(url + '\n')
        print(f"[{prefix}] {len(new_urls)}개의 새 링크가 추가되었습니다.")
    else:
        print(f"[{prefix}] 새로운 게시물이 없습니다.")
        
    return new_urls