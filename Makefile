.PHONY: demo lint test
demo:
	benchctl up --profile std
lint:
	ruff check .
test:
	pytest
