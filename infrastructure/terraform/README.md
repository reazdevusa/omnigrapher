# Phase 1 Terraform Infrastructure

## What it provisions
- **VPC** with public/private subnets across 2 AZs
- **S3 bucket** for uploaded documents (versioned, private)
- **RDS PostgreSQL** for transactional data (users, jobs, billing)
- **ElastiCache Redis** for queues, rate limits, and caching
- **Application Load Balancer + ECS Fargate** for the FastAPI app
- **ECR repository** for the container image

## Usage

1. Install Terraform and configure AWS credentials.
2. Copy the example variables file:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```
3. Edit `terraform.tfvars` and set a strong `db_password`.
4. Initialize and apply:
   ```bash
   terraform init
   terraform plan
   terraform apply
   ```
5. Build and push the FastAPI Docker image to ECR, then update `app_container_image` or use the `:latest` tag.
6. Point the Next.js frontend to the `alb_dns_name` output.

## Notes
- This stack intentionally uses **public IP Fargate tasks** to avoid NAT Gateway cost in early phases.
- Add HTTPS listener + ACM certificate before production traffic.
- Store LLM provider API keys in AWS Secrets Manager and reference them in `container_definitions`.
