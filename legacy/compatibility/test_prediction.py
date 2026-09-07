"""Call the running Flask service with two example rows."""

import requests


def main() -> None:
    response = requests.post(
        "http://127.0.0.1:8000/predict",
        json={
            "channel": 0,
            "data": [
                [1.573, 4.598, 1.439, 0.919],
                [2.100, 3.500, 1.100, 1.200],
            ],
        },
        timeout=30,
    )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
