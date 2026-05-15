# HESLNK Tool 사용법

`heslnk_tool.py`는 `script.heslnk`를 분석, 언팩, 리팩, 검증하는 단일 CLI 도구다.

## 기본 명령

```bash
python3 heslnk_tool.py inspect script.heslnk
python3 heslnk_tool.py unpack script.heslnk script_out
python3 heslnk_tool.py repack script_out script_new.heslnk --reuse-from script.heslnk
python3 heslnk_tool.py verify script_out script_new.heslnk
```

## 언팩

```bash
python3 heslnk_tool.py unpack script.heslnk script_out
```

언팩 결과는 `script_out/*.hese`로 저장된다. 함께 생성되는
`script_out/.heslnk_manifest.json`에는 원본 엔트리 순서, 해시, HLZS 버전, 원본
검증 해시가 들어 있다.

## 리팩

```bash
python3 heslnk_tool.py repack script_out script_new.heslnk --reuse-from script.heslnk
```

`--reuse-from`을 주면 수정되지 않은 `.hese`는 원본 HLZS 블록을 그대로 복사하고,
수정된 `.hese`만 새로 압축한다. 아무 파일도 수정하지 않았다면 원본과 바이트 단위로
같은 `.heslnk`를 다시 만들 수 있다.

기존 `script_out/`처럼 manifest가 없는 폴더도 `--reuse-from script.heslnk`를 함께
주면 원본 아카이브에서 엔트리 순서와 메타데이터를 읽어 리팩할 수 있다.

## 검증

```bash
python3 heslnk_tool.py verify script_out script_new.heslnk
```

리팩된 아카이브를 다시 압축 해제했을 때 `script_out/*.hese`와 모두 같으면 `OK`가
출력된다.

## 참고 구조

- HESL 헤더 크기: 48바이트
- 엔트리 크기: 16바이트
- 엔트리 값: `crc32(name)`, HLZS 블록 offset, 압축 해제 크기
- 이름 테이블: NUL 종료 UTF-8 문자열
- 데이터 블록: HLZS 압축 HESE, 0x20 정렬
