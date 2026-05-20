# Manual EC2 Deployment Runbook

This runbook provisions and tests `careround-ai` on EC2 without Terraform. It also shows how to verify the AI service before `careround-core` is deployed.

### Docker command to start image:
```
docker run --rm -p 8000:8000 -e OLLAMA_HOST=http://host.docker.internal:11434 -e AI_PROVIDER=stub -e WHISPER_MODEL=base.en -e LLM_MODEL=llama3.2:3b careround-ai:local
```

## Key Notes

- `careround-ai` exposes `POST /process-voice-note`, not `/api/process-voice-note`.
- GitHub-hosted Actions cannot SSH to a private EC2 IP unless you provide a bastion, VPN, SSM path, or self-hosted runner inside the VPC.
- For the fastest MVP/demo setup, use a public EC2 instance locked down to your IP, then move private later when the rest of the VPC/core service is ready.
- Deploy in stub mode first. This verifies EC2, Docker, ECR, CloudWatch logs, port `8000`, and FastAPI without waiting for Ollama or Whisper model downloads.

## Recommended EC2 Shape

- AMI: Amazon Linux 2023
- Instance type: `t3.large` for CPU demo
- Root volume: `30-50 GB`, preferably `gp3`
- Security group:
  - TCP `22` from your IP only
  - TCP `8000` from your IP for direct testing, or from the core service security group later
  - TCP `11434` should not be public
- IAM instance profile:
  - ECR pull permissions
  - CloudWatch logs permissions if using Docker `awslogs`
  - Optional SSM permissions for Session Manager access

## 1. Find Latest Amazon Linux 2023 AMI

```bash
AMI_ID=$(aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=al2023-ami-*-x86_64" \
  --query "sort_by(Images, &CreationDate)[-1].ImageId" \
  --output text)

echo "$AMI_ID"
```

## 2. Launch EC2 Manually

Use a public subnet for the quick setup if GitHub Actions will SSH directly to the instance.

```bash
aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t3.large \
  --key-name careround-keypair \
  --security-group-ids <your-ai-sg-id> \
  --subnet-id <your-public-subnet-id> \
  --associate-public-ip-address \
  --iam-instance-profile Name=careround-ec2-profile \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":50,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=careround-ai}]' \
  --count 1
```

Wait for it:

```bash
aws ec2 wait instance-running \
  --filters "Name=tag:Name,Values=careround-ai"
```

Get the instance IPs:

```bash
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=careround-ai" "Name=instance-state-name,Values=running" \
  --query "Reservations[*].Instances[*].[InstanceId,PublicIpAddress,PrivateIpAddress]" \
  --output table
```

Use the public IP for direct SSH/GitHub Actions if this is a public demo instance. Use the private IP later for `careround-core` to call the service inside the VPC.

## 3. Install Docker

```bash
ssh -i careround-ai-keypair.pem ec2-user@<ai-public-ip>
```

```bash
sudo yum update -y
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user
```

Log out and back in so the Docker group change applies:

```bash
exit
ssh -i careround-ai-keypair.pem ec2-user@<ai-public-ip>
docker --version
```

## 4. Create ECR Repository

```bash
aws ecr create-repository \
  --repository-name careround-ai \
  --region us-east-1 \
  --image-scanning-configuration scanOnPush=true
```

The image URI will be:

```text
<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/careround-ai
```

## 5. Deploy Stub Mode First

Stub mode should be the first deployment because it does not require Ollama or Whisper model downloads.

Environment:

```bash
AI_PROVIDER=stub
TRANSCRIPTION_PROVIDER=stub
```

Example manual Docker run on EC2 after pulling the image:

```bash
AWS_REGION=us-east-1
AWS_ACCOUNT_ID=<account-id>
ECR_REGISTRY=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
IMAGE_NAME=careround-ai

aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY

docker pull $ECR_REGISTRY/$IMAGE_NAME:latest

docker stop careround-ai 2>/dev/null || true
docker rm careround-ai 2>/dev/null || true

docker run -d \
  --name careround-ai \
  --restart unless-stopped \
  -p 8000:8000 \
  -e AI_PROVIDER=stub \
  -e TRANSCRIPTION_PROVIDER=stub \
  -e OLLAMA_HOST=http://localhost:11434 \
  -e WHISPER_MODEL=base.en \
  -e LLM_MODEL=llama3.2:3b \
  --log-driver awslogs \
  --log-opt awslogs-region=$AWS_REGION \
  --log-opt awslogs-group=/careround/ai \
  --log-opt awslogs-create-group=true \
  $ECR_REGISTRY/$IMAGE_NAME:latest
```

If you use `--network host` instead of `-p 8000:8000`, the service will still be available on `localhost:8000` from the EC2 host.

## 6. Test Without `careround-core`

From the EC2 host:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ready","whisperLoaded":true,"llmLoaded":true}
```

Test ward-round mode:

```bash
curl -X POST http://localhost:8000/process-voice-note \
  -F "audio=@/dev/null;type=audio/wav" \
  -F "patient_id=test-123" \
  -F "current_time=2026-05-19T10:00:00" \
  -F "mode=ward_round"
```

Test handover/free-note mode:

```bash
curl -X POST http://localhost:8000/process-voice-note \
  -F "audio=@/dev/null;type=audio/wav" \
  -F "patient_id=test-123" \
  -F "current_time=2026-05-19T10:00:00" \
  -F "mode=transcription_only"
```

If port `8000` is open to your IP, test from your machine:

```bash
curl http://<ai-public-ip>:8000/health
```

Check container state and logs:

```bash
docker ps
docker logs careround-ai --tail 50
```

## 7. Optional: Install Ollama For Real LLM Testing

Only do this after stub mode works.

```bash
curl -fsSL https://ollama.com/install.sh | sh

sudo systemctl enable ollama
sudo systemctl start ollama

curl http://localhost:11434/api/tags
```

Pull models:

```bash
ollama pull mistral
ollama pull llama3.2:3b
ollama list
```

If the container uses `--network host`, `OLLAMA_HOST=http://localhost:11434` is enough.

If the container uses Docker bridge networking, configure Ollama to bind beyond localhost:

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d/
sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF

sudo systemctl daemon-reload
sudo systemctl restart ollama
curl http://localhost:11434/api/tags
```

## 8. Run With Real Models

After Ollama models are pulled and the stub deployment is proven:

```bash
sudo mkdir -p /opt/careround-ai/hf-cache

docker stop careround-ai 2>/dev/null || true
docker rm careround-ai 2>/dev/null || true

docker run -d \
  --name careround-ai \
  --restart unless-stopped \
  --network host \
  -v /opt/careround-ai/hf-cache:/root/.cache/huggingface \
  -e AI_PROVIDER=ollama \
  -e TRANSCRIPTION_PROVIDER=whisper \
  -e OLLAMA_HOST=http://localhost:11434 \
  -e WHISPER_MODEL=base.en \
  -e LLM_MODEL=mistral \
  $ECR_REGISTRY/$IMAGE_NAME:latest
```

Then test:

```bash
curl http://localhost:8000/health
docker logs careround-ai --tail 100
```

The first real Whisper run may download/cache the Whisper model. Health may stay `loading` until both Whisper and the LLM are ready.

The mounted cache directory keeps the Whisper model on the EC2 host across container restarts:

```text
/opt/careround-ai/hf-cache
```

## 9. GitHub Actions Notes

If using `appleboy/ssh-action` from GitHub-hosted runners:

- `AI_EC2_HOST` must be publicly reachable, or you must use a bastion/SSM/self-hosted runner.
- Store the full private key contents in `EC2_SSH_KEY`.
- Use the EC2 public IP as `AI_EC2_HOST` for the quick public-instance setup.
- Lock security group SSH access as tightly as possible.

Recommended first deployment environment:

```yaml
-e AI_PROVIDER=stub
-e TRANSCRIPTION_PROVIDER=stub
```

Real model deployment environment:

```yaml
-e AI_PROVIDER=ollama
-e TRANSCRIPTION_PROVIDER=whisper
-e OLLAMA_HOST=http://localhost:11434
-e WHISPER_MODEL=${{ secrets.WHISPER_MODEL }}
-e LLM_MODEL=${{ secrets.LLM_MODEL }}
```

Mount a persistent Whisper cache volume:

```yaml
-v /opt/careround-ai/hf-cache:/root/.cache/huggingface
```

## 10. Later `careround-core` Integration

When `careround-core` is deployed in the same VPC, configure:

```bash
AI_SERVICE_URL=http://<ai-private-ip>:8000
```

Then from the core instance:

```bash
curl http://<ai-private-ip>:8000/health
```

The core service should expose its own client-facing endpoint:

```text
POST /api/v1/ai/process-voice-note
```

and proxy internally to:

```text
POST http://<ai-private-ip>:8000/process-voice-note
```
