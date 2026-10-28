.PHONY: demo lint test
demo:
	simctl up --profile std
lint:
	ruff check .
test:
	pytest
