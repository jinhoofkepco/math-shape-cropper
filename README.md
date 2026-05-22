# 수학 문제집 도형 크롭 보조 앱

문제집 사진에서 도형 영역을 대략 드래그하면, Python으로 경계를 조금 보정하고 흑백 대비를 정리해서 저장하는 보조 앱입니다.

## 기능

- 왼쪽: 선택한 폴더의 문제집 사진 목록을 세로로 표시
- 가운데: 선택한 문제 페이지 표시 및 드래그 크롭
- 오른쪽: 보정된 크롭 미리보기 목록
- 오른쪽 상단: 공통 이름 입력
- 각 크롭 아래: 개별 후속 이름 입력
- 저장 버튼 왼쪽: 저장 폴더명 입력
- 저장 시: `공통이름_개별이름.png` 형식으로 전체 저장
- 저장 시: 원본 파일과 보정 좌표를 담은 `manifest.json`도 함께 저장

## 실행

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run_app.py
```

특정 이미지 폴더를 바로 열고 싶으면:

```powershell
python run_app.py "C:\문제집사진폴더"
```

## 사용 방법

1. `폴더 선택`으로 문제집 사진 폴더를 고릅니다.
2. 왼쪽 목록에서 페이지를 선택합니다.
3. 가운데 페이지 화면에서 도형을 대략 드래그합니다.
4. 오른쪽에 보정된 크롭이 추가되면 공통 이름과 개별 이름을 설정합니다.
5. 저장 폴더명을 입력하고 `저장`을 누릅니다.

## 보정 방식

OpenCV 없이도 실행되도록 Pillow와 NumPy만 사용합니다. 사용자가 선택한 영역 안에서 어두운 선과 글자를 기준으로 전경 경계를 찾고, 약간의 여백을 둔 뒤 흑백 대비와 선명도를 보정합니다.

## GitHub 관리

현재 프로젝트는 로컬 git 저장소로 관리할 수 있습니다. 새 GitHub 저장소를 만든 뒤 아래처럼 원격을 연결하면 됩니다.

```powershell
git remote add origin https://github.com/사용자명/저장소명.git
git branch -M main
git push -u origin main
```
