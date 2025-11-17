# AWS Infrastructure Setup for embot

This guide helps you set up the AWS infrastructure needed to run the Poshmark scraper on Lambda.

## Prerequisites

1. AWS CLI installed and configured
2. Appropriate AWS permissions (IAM, Lambda, S3, EventBridge)
3. Python 3.11+ for packaging

## Quick Setup

Run the setup script:

```bash
chmod +x setup_aws_infrastructure.sh
./setup_aws_infrastructure.sh
```

Or manually run the commands below.

## Manual Setup

### 1. Create S3 Bucket

```bash
BUCKET_NAME="embot-poshmark-listings"
REGION="us-east-1"

aws s3api create-bucket \
    --bucket "${BUCKET_NAME}" \
    --region "${REGION}"

# Enable versioning
aws s3api put-bucket-versioning \
    --bucket "${BUCKET_NAME}" \
    --versioning-configuration Status=Enabled
```

### 2. Create IAM Role

```bash
IAM_ROLE_NAME="embot-lambda-role"

# Create trust policy
cat > trust-policy.json <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "lambda.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
EOF

# Create role
aws iam create-role \
    --role-name "${IAM_ROLE_NAME}" \
    --assume-role-policy-document file://trust-policy.json

# Attach basic Lambda execution policy
aws iam attach-role-policy \
    --role-name "${IAM_ROLE_NAME}" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Create S3 access policy
cat > s3-policy.json <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::${BUCKET_NAME}",
                "arn:aws:s3:::${BUCKET_NAME}/*"
            ]
        }
    ]
}
EOF

aws iam put-role-policy \
    --role-name "${IAM_ROLE_NAME}" \
    --policy-name "embot-s3-access" \
    --policy-document file://s3-policy.json

# Get role ARN
ROLE_ARN=$(aws iam get-role --role-name "${IAM_ROLE_NAME}" --query 'Role.Arn' --output text)
echo "Role ARN: ${ROLE_ARN}"
```

### 3. Package Lambda Function

```bash
# Create deployment package
mkdir -p lambda-package
pip install -r requirements.txt -t lambda-package/
cp poshmark_scraper.py lambda-package/
cd lambda-package
zip -r ../embot-poshmark-scraper.zip .
cd ..
```

### 4. Create Lambda Function

```bash
LAMBDA_FUNCTION_NAME="embot-poshmark-scraper"
BUCKET_NAME="embot-poshmark-listings"

aws lambda create-function \
    --function-name "${LAMBDA_FUNCTION_NAME}" \
    --runtime python3.11 \
    --role "${ROLE_ARN}" \
    --handler poshmark_scraper.lambda_handler \
    --zip-file fileb://embot-poshmark-scraper.zip \
    --timeout 900 \
    --memory-size 512 \
    --environment Variables="{S3_BUCKET=${BUCKET_NAME}}" \
    --description "Poshmark scraper for embot"
```

### 5. Create EventBridge Rule (Optional - for scheduled runs)

```bash
RULE_NAME="embot-daily-scrape"

# Create rule (runs daily at 2 AM UTC)
aws events put-rule \
    --name "${RULE_NAME}" \
    --schedule-expression "cron(0 2 * * ? *)" \
    --description "Daily scrape at 2 AM UTC"

# Add Lambda as target
LAMBDA_ARN=$(aws lambda get-function --function-name ${LAMBDA_FUNCTION_NAME} --query 'Configuration.FunctionArn' --output text)

aws events put-targets \
    --rule "${RULE_NAME}" \
    --targets "Id=1,Arn=${LAMBDA_ARN},Input='{\"username\":\"emily2636\",\"s3_bucket\":\"${BUCKET_NAME}\",\"s3_prefix\":\"emily2636\",\"incremental\":true,\"delay\":1.0}'"

# Grant EventBridge permission to invoke Lambda
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="us-east-1"

aws lambda add-permission \
    --function-name "${LAMBDA_FUNCTION_NAME}" \
    --statement-id "embot-eventbridge-invoke" \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_NAME}"
```

## Testing

### Test Lambda Function Locally

```bash
# Test with sample event
aws lambda invoke \
    --function-name embot-poshmark-scraper \
    --payload '{"username":"emily2636","s3_bucket":"embot-poshmark-listings","s3_prefix":"emily2636","incremental":true,"delay":1.0}' \
    response.json

cat response.json
```

### View Logs

```bash
aws logs tail /aws/lambda/embot-poshmark-scraper --follow
```

## Updating Lambda Function

When you make changes to the code:

```bash
# Package
mkdir -p lambda-package
pip install -r requirements.txt -t lambda-package/
cp poshmark_scraper.py lambda-package/
cd lambda-package
zip -r ../embot-poshmark-scraper.zip .
cd ..

# Update
aws lambda update-function-code \
    --function-name embot-poshmark-scraper \
    --zip-file fileb://embot-poshmark-scraper.zip
```

## Cost Estimation

- **Lambda**: Free tier includes 1M requests/month and 400,000 GB-seconds
- **S3**: ~$0.023 per GB/month (first 50 TB)
- **EventBridge**: Free tier includes 14M custom events/month
- **CloudWatch Logs**: First 5 GB/month free

For ~5,500 listings, estimated monthly cost: **< $1** (mostly S3 storage)

## Monitoring

### View Lambda Metrics

```bash
aws cloudwatch get-metric-statistics \
    --namespace AWS/Lambda \
    --metric-name Invocations \
    --dimensions Name=FunctionName,Value=embot-poshmark-scraper \
    --start-time $(date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%S) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
    --period 3600 \
    --statistics Sum
```

### Check S3 Bucket Contents

```bash
aws s3 ls s3://embot-poshmark-listings/emily2636/ --recursive | wc -l
```

## Troubleshooting

### Lambda Timeout

If scraping takes too long, increase timeout:
```bash
aws lambda update-function-configuration \
    --function-name embot-poshmark-scraper \
    --timeout 900
```

### Permission Errors

Check IAM role policies:
```bash
aws iam list-role-policies --role-name embot-lambda-role
aws iam get-role-policy --role-name embot-lambda-role --policy-name embot-s3-access
```

### View Recent Errors

```bash
aws logs filter-log-events \
    --log-group-name /aws/lambda/embot-poshmark-scraper \
    --filter-pattern "ERROR" \
    --max-items 10
```

