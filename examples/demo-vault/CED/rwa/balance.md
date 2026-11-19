---
product: "rwa"
keywords: ["account","balance","user"]
appearsIn: ["bank_accounts.balance","users.balance"]
tables: ["bank_accounts","users"]
status: confirmed
updated: "2026-11-15T23:24:30.462Z"
---
# balance

목차: [[rwa]]

## 정의

개인 User 대상 계좌 잔액 기준 총 금액의 합산

## 출현 목록

- `bank_accounts.csv` · `balance` (integer, NN)
- `users.csv` · `balance` (integer, NN)

## 값의 뜻

개별 값은 계좌 혹은 User가 가진 잔액을 나타낸다. 주체는 계좌일 수 있으며, User의 계좌 합산 금액의 총액 (Sum)일 수 있다.

## 설명

1. 개별 계좌는 잔액 값을 가진다. 그 잔액 값을 의미한다.
2. 유저가 여러 계좌를 가지고 있고, 개별 계좌에 잔액이 있을 때. 그 합산 수치로도 쓰인다.
