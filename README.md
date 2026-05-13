# HDLG Excel 변환 사용법

## 설치

```bash
python3 -m pip install -r requirements.txt
```

## 1. hdlg를 xlsx로 만들기

```bash
python3 tools/hdlg_to_xlsx.py script_dialog_ja.hdlg script_dialog_ja.xlsx --reference script_dialog_en.hdlg
```

생성된 `script_dialog_ja.xlsx`에서 `dialog` 시트의 `text` 칼럼만 수정하면 된다.
`text`가 비어 있으면 자동으로 `ja_original`을 사용한다.

기본 칼럼은 `id`, `ja_original`, `text`, `en_reference`, `notes`다.

## 2. xlsx를 hdlg로 만들기

```bash
python3 tools/xlsx_to_hdlg.py script_dialog_ja.xlsx script_dialog_ja_new.hdlg
```

내가 만든 엑셀 파일명이 다르면 앞쪽 파일명만 바꾸면 된다.

```bash
python3 tools/xlsx_to_hdlg.py 내파일.xlsx 결과.hdlg
```

## 3. hdlg 검증하기

```bash
python3 tools/verify_hdlg_roundtrip.py script_dialog_ja_new.hdlg
```

`OK`가 나오면 정상이다.

## 주의

- `id` 칼럼은 수정하지 말 것
- 행을 추가하거나 삭제하지 말 것
- `text`가 채워진 행만 원문 대신 `text` 값이 반영됨
- `<br>`, `<firstname>` 같은 태그는 가능하면 유지할 것
