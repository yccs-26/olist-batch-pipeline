import argparse
from datetime import datetime

def parse_args() -> argparse.Namespace:
    # batch_year, batch_month parsing
    parser = argparse.ArgumentParser(
        description="S3 orders 월별 파티션을 PostgreSQL staging에 적재"
    )

    parser.add_argument(
        "--batch-date",
        type=str,
        required=True,
        help="배치 기준월 (YYYY-MM 형식, 예: 2017-01)"
    )

    args = parser.parse_args()

    try:
        parsed = datetime.strptime(args.batch_date, "%Y-%m")
    except ValueError:
        parser.error("--batch-date는 YYYY-MM 형식이어야 한다. 예: 2017-01")

    args.year = parsed.year
    args.month = parsed.month

    return args

# * test
# if __name__ == "__main__":
#     args = parse_args()

#     print(f"{args.batch_date = }")
#     print(f"{args.year = }")
#     print(f"{args.month = }")