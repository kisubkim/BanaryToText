# BinaryToText

알 수 없는 바이너리 파일을 사람이 읽을 수 있는 텍스트로 바꾸는 **오프라인 데스크톱 앱**입니다 (Windows, PySide6).
브라우저나 네트워크 없이 동작합니다.

해독 방식을 모르는 파일도 다룰 수 있게 만들었습니다. 파일을 열고, 자동 분석으로 단서를 얻고, 규칙을
하나씩 추가하면 결과가 바로 다시 해석되어 텍스트로 나옵니다.

## 실행

| 방법 | 명령 |
|---|---|
| 배포 exe | `dist\BinaryToText.exe [파일]` (GUI), `dist\btt.exe` (명령줄) |
| 소스 | `.venv\Scripts\python -m binarytotext [파일]` |
| 빌드 | `powershell -ExecutionPolicy Bypass -File build.ps1` (테스트 후 두 exe 생성) |

## 화면 구성

- **왼쪽 hex 보기**: 주황색 = 아직 어떤 규칙에도 맞지 않은 byte, 파란색 = 선택한 항목.
  바이트를 선택하고 우클릭 → *태그로 규칙 추가*.
  아래 **데이터 해석기**는 커서 위치의 값을 uint/int(8~64, LE/BE), float, varint, Unix 시각, BCD,
  ASCII/UTF-8/CP949/UTF-16 으로 한꺼번에 보여 줍니다.
- **가운데 탭**: 규칙 / 포맷 옵션 / 자동 분석
- **오른쪽 결과**: 텍스트와 트리. 텍스트 줄을 클릭하면 hex 위치가, hex 를 클릭하면 해당 텍스트 줄이 표시됩니다.
- 툴바: 해석 방식(사용자 규칙 또는 내장 포맷), 전처리, 오프셋/원본 hex 표시, 텍스트·JSON 저장.

## 사용자 규칙

"이 byte(들)가 나오면 그 다음을 이렇게 읽는다"는 규칙의 목록입니다. 파일을 앞에서부터 읽으면서
가장 긴 태그가 맞는 규칙을 적용하고, 맞는 규칙이 없으면 미해석 byte 로 모읍니다.

**태그**: `09`, `5F 20` (여러 byte), `0A??` (`??` = 아무 byte), `"STR"` (ASCII 그대로)

**동작**

| 동작 | 의미 |
|---|---|
| 값 읽기 | 태그 다음의 필드들을 읽어 `이름: 값` 한 줄로 출력. 필드가 없으면 이름만 출력 (1 byte → 글자) |
| 그룹 열기 / 닫기 | 배열·구조체 시작과 끝. 안쪽은 들여쓰기 |
| 그룹 열기/닫기 토글 | 같은 태그로 열고 닫음 (`0A ... 0A`) |
| 길이 블록 | 첫 필드가 길이. 그만큼을 그룹 안쪽으로 해석 (TLV) |
| 개수 블록 | 첫 필드가 항목 수. 그만큼의 항목을 그룹 안쪽으로 해석 |
| 해석 종료 | 여기서 멈춤 |

**필드 타입**: 정수(1~16 byte, 부호, 엔디언, 10/16/2진 표시, 값 이름표 `1=ON`), 실수(2/4/8),
불리언, 종료 byte 까지의 문자열(종료 byte 지정 가능, UTF-16 은 `0000`), 고정 길이 문자열,
길이 접두 문자열(1/2/4 byte 또는 varint), 바이트(hex), BCD, varint, zigzag varint, Unix 시각(초/밀리초),
건너뛰기, 나머지 전부. 문자 인코딩은 ASCII, UTF-8, CP949/EUC-KR, UTF-16, Shift-JIS, GBK, Big5 등.

규칙 세트는 JSON 으로 저장·불러오기 합니다 (`examples/` 참고). 창에 JSON 파일을 끌어다 놓아도 됩니다.
마지막 규칙 세트는 자동으로 기억됩니다.

## 내장 포맷 (이미 알려진 방식)

| 계열 | 포맷 |
|---|---|
| TLV | 범용 TLV/LTV/LV (태그 1/2/4, 길이 1/2/4/BER/varint, 엔디언, 헤더 포함 여부, 중첩 추정), BER-TLV·ASN.1 DER/BER (X.509 인증서, PEM 자동 변환, OID 이름), EMV/스마트카드 BER-TLV (태그 사전) |
| 직렬화 | Protocol Buffers (스키마 없이), MessagePack, CBOR, BSON, UBJSON, Bencode, Thrift Binary, Thrift Compact |
| 기본 | Hex 덤프, 문자열 추출 (ASCII/UTF-8/UTF-16/CP949) |

**전처리**: 앞 N byte 건너뛰기, zlib/raw deflate/gzip/bzip2/xz 압축 해제, Base64·hex 텍스트 → 바이트, XOR(키 hex).
전처리를 쓰면 오프셋은 변환된 데이터 기준입니다.

## 자동 분석 (F6)

- 엔트로피 (압축/암호화 여부 추정), 파일 시그니처 40여 종, 가능한 전처리
- 반복 주기 → 고정 길이 레코드 크기 후보
- 모든 내장 포맷을 시도해 적합도 순으로 정렬 → 더블클릭으로 바로 적용
- **태그 후보**: 문자열 바로 앞에 반복해서 나오는 byte 를 찾아 규칙을 제안
  (`00 종료 문자열`, `1 byte 길이 + 문자열`, `2 byte 길이 + 문자열`) → 선택해서 규칙에 추가
- 문자열 목록 (앞 3 byte 함께 표시, 더블클릭 → hex 위치)

적합도는 "얼마나 많은 byte 를 오류 없이 설명하는가"에 기반하며, 1 byte 짜리 항목만 잔뜩 나오거나
최상위 객체가 여러 개인 해석은 잡음으로 보고 점수를 깎습니다.

## 명령줄 (`btt.exe` 또는 `python -m binarytotext`)

```
btt decode 파일 --rules 규칙.json [-o 결과.txt] [--no-offsets] [--raw] [--json]
btt decode 파일 --format protobuf --opt nested=true --pre zlib
btt analyze 파일
btt formats            # 내장 포맷과 옵션 목록
```

## 포맷 추가하기

`binarytotext/formats/` 에 모듈을 만들고 `register(Decoder(...))` 를 호출한 뒤
`formats/__init__.py` 에서 import 합니다. 디코더는 `Node` 트리를 돌려주면 되고, GUI 옵션 화면과
자동 분석은 등록 정보로 자동 구성됩니다.

## 라이선스

MIT
