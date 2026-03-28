.PHONY: build push run run-cpu stop logs shell test health fmt lint

IMAGE := ebennerv/athena-tts
TAG := latest
AUTH_TOKEN ?= test-token
WORKSPACE ?= $(PWD)/workspace
MODEL_CACHE ?= $(PWD)/model-cache

build:
	docker buildx build --platform linux/amd64 -t $(IMAGE):$(TAG) --load .

push: build
	docker push $(IMAGE):$(TAG)

run:
	@mkdir -p $(WORKSPACE) $(MODEL_CACHE)
	docker run -d \
		--name athena-tts \
		-p 5002:5002 \
		-e AUTH_TOKEN=$(AUTH_TOKEN) \
		-v $(WORKSPACE):/workspace \
		-v $(MODEL_CACHE):/root/.local/share/tts \
		--gpus all \
		$(IMAGE):$(TAG)

run-cpu:
	@mkdir -p $(WORKSPACE) $(MODEL_CACHE)
	docker run -d \
		--name athena-tts \
		-p 5002:5002 \
		-e AUTH_TOKEN=$(AUTH_TOKEN) \
		-v $(WORKSPACE):/workspace \
		-v $(MODEL_CACHE):/root/.local/share/tts \
		$(IMAGE):$(TAG)

stop:
	docker stop athena-tts && docker rm athena-tts

logs:
	docker logs -f athena-tts

shell:
	docker exec -it athena-tts /bin/bash

health:
	curl -s http://localhost:5002/health

test:
	@echo "Testing with existing speaker 'test'..."
	curl -X POST http://localhost:5002/api/tts \
		-H "Authorization: Bearer $(AUTH_TOKEN)" \
		-F "text=Hello, this is a test." \
		-F "speaker=test" \
		--output test-output.wav
	@echo "\nSaved to test-output.wav"

test-upload:
	@echo "Testing speaker upload..."
	curl -X POST http://localhost:5002/api/tts \
		-H "Authorization: Bearer $(AUTH_TOKEN)" \
		-F "text=Hello, this is a test." \
		-F "speaker_file=@$(SPEAKER_FILE)" \
		--output test-output.wav
	@echo "\nSaved to test-output.wav"

fmt:
	black server.py

lint:
	black --check server.py
