#!/bin/bash
# AWS Infrastructure Setup for embot Poshmark Scraper
# This script creates the necessary AWS resources for running the scraper on Lambda
# This script is idempotent - it can be run multiple times safely

# Don't exit on error - we'll handle errors gracefully
set +e

PROJECT_NAME="embot"
BUCKET_NAME="${PROJECT_NAME}-poshmark-listings"
LAMBDA_FUNCTION_NAME="${PROJECT_NAME}-poshmark-scraper"
IAM_ROLE_NAME="${PROJECT_NAME}-lambda-role"
REGION="us-east-1"  # Change to your preferred region

echo "🚀 Setting up AWS infrastructure for ${PROJECT_NAME}..."
echo ""

# 1. Create S3 bucket
echo "📦 Creating S3 bucket: ${BUCKET_NAME}"
if [ "${REGION}" = "us-east-1" ]; then
    # us-east-1 doesn't need LocationConstraint
    aws s3api create-bucket \
        --bucket "${BUCKET_NAME}" \
        --region "${REGION}" || echo "⚠️  Bucket may already exist"
else
    aws s3api create-bucket \
        --bucket "${BUCKET_NAME}" \
        --region "${REGION}" \
        --create-bucket-configuration LocationConstraint="${REGION}" || echo "⚠️  Bucket may already exist"
fi

# Enable versioning (optional but recommended)
aws s3api put-bucket-versioning \
    --bucket "${BUCKET_NAME}" \
    --versioning-configuration Status=Enabled

# Add lifecycle policy to transition old versions (optional)
cat > /tmp/lifecycle.json <<EOF
{
    "Rules": [
        {
            "ID": "DeleteOldVersions",
            "Status": "Enabled",
            "NoncurrentVersionExpiration": {
                "NoncurrentDays": 30
            }
        }
    ]
}
EOF
aws s3api put-bucket-lifecycle-configuration \
    --bucket "${BUCKET_NAME}" \
    --lifecycle-configuration file:///tmp/lifecycle.json || echo "⚠️  Lifecycle policy may already exist or bucket may not support it"

# Enable static website hosting
echo "🌐 Enabling static website hosting..."
cat > /tmp/website-config.json <<EOF
{
    "IndexDocument": {
        "Suffix": "index.html"
    },
    "ErrorDocument": {
        "Key": "index.html"
    }
}
EOF
aws s3api put-bucket-website \
    --bucket "${BUCKET_NAME}" \
    --website-configuration file:///tmp/website-config.json || echo "⚠️  Website configuration may already exist"

# Create bucket policy to allow public read access only to index.html files
echo "🔓 Setting bucket policy for public index.html access..."
cat > /tmp/bucket-policy.json <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublicReadGetObject",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::${BUCKET_NAME}/*/index.html"
        }
    ]
}
EOF
aws s3api put-bucket-policy \
    --bucket "${BUCKET_NAME}" \
    --policy file:///tmp/bucket-policy.json || echo "⚠️  Bucket policy may already exist"

echo "✅ S3 bucket created: s3://${BUCKET_NAME}"
echo "✅ Static website hosting enabled"
echo "   Website URL: http://${BUCKET_NAME}.s3-website-${REGION}.amazonaws.com"
echo "   (Note: Each prefix/closet will have its own index.html)"
echo ""

# 2. Create IAM role for Lambda
echo "🔐 Creating IAM role: ${IAM_ROLE_NAME}"

# Trust policy for Lambda
cat > /tmp/trust-policy.json <<EOF
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

# Create the role (or update if exists)
aws iam create-role \
    --role-name "${IAM_ROLE_NAME}" \
    --assume-role-policy-document file:///tmp/trust-policy.json \
    --description "IAM role for ${PROJECT_NAME} Lambda function" 2>/dev/null

if [ $? -ne 0 ]; then
    echo "⚠️  Role already exists, updating trust policy..."
    aws iam update-assume-role-policy \
        --role-name "${IAM_ROLE_NAME}" \
        --policy-document file:///tmp/trust-policy.json
else
    # Wait for role to be available
    echo "⏳ Waiting for IAM role to be available..."
    sleep 5
fi

# Attach basic Lambda execution policy (idempotent - won't fail if already attached)
aws iam attach-role-policy \
    --role-name "${IAM_ROLE_NAME}" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || echo "⚠️  Policy may already be attached"

# Create and attach S3 access policy
cat > /tmp/s3-policy.json <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObject",
                "s3:ListBucket",
                "s3:DeleteObject"
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
    --policy-name "${PROJECT_NAME}-s3-access" \
    --policy-document file:///tmp/s3-policy.json

# Get the role ARN
ROLE_ARN=$(aws iam get-role --role-name "${IAM_ROLE_NAME}" --query 'Role.Arn' --output text)
echo "✅ IAM role created: ${ROLE_ARN}"
echo ""

# 3. Package Lambda function
echo "📦 Packaging Lambda function..."
# Create deployment package
zip -r /tmp/${LAMBDA_FUNCTION_NAME}.zip poshmark_scraper.py

# If you have a requirements.txt, install dependencies
if [ -f requirements.txt ]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt -t /tmp/lambda-package/
    cp poshmark_scraper.py /tmp/lambda-package/
    cd /tmp/lambda-package
    zip -r /tmp/${LAMBDA_FUNCTION_NAME}.zip .
    cd -
fi

echo "✅ Lambda package created: /tmp/${LAMBDA_FUNCTION_NAME}.zip"
echo ""

# 4. Create Lambda function
echo "⚡ Creating Lambda function: ${LAMBDA_FUNCTION_NAME}"

aws lambda create-function \
    --function-name "${LAMBDA_FUNCTION_NAME}" \
    --runtime python3.11 \
    --role "${ROLE_ARN}" \
    --handler poshmark_scraper.lambda_handler \
    --zip-file fileb:///tmp/${LAMBDA_FUNCTION_NAME}.zip \
    --timeout 900 \
    --memory-size 1024 \
    --environment Variables="{S3_BUCKET=${BUCKET_NAME}}" \
    --description "Poshmark scraper for ${PROJECT_NAME}" \
    --region "${REGION}" 2>/dev/null

if [ $? -ne 0 ]; then
    echo "⚠️  Function already exists, updating code and configuration..."
    aws lambda update-function-code \
        --function-name "${LAMBDA_FUNCTION_NAME}" \
        --zip-file fileb:///tmp/${LAMBDA_FUNCTION_NAME}.zip \
        --region "${REGION}"
    
    aws lambda update-function-configuration \
        --function-name "${LAMBDA_FUNCTION_NAME}" \
        --timeout 900 \
        --memory-size 1024 \
        --environment Variables="{S3_BUCKET=${BUCKET_NAME}}" \
        --description "Poshmark scraper for ${PROJECT_NAME}" \
        --region "${REGION}" 2>/dev/null || echo "⚠️  Configuration update may have failed"
fi

# Get the actual region from the Lambda function (in case it was created in a different region)
LAMBDA_ARN=$(aws lambda get-function --function-name "${LAMBDA_FUNCTION_NAME}" --region "${REGION}" --query 'Configuration.FunctionArn' --output text 2>/dev/null || \
    aws lambda get-function --function-name "${LAMBDA_FUNCTION_NAME}" --query 'Configuration.FunctionArn' --output text)
ACTUAL_REGION=$(echo "${LAMBDA_ARN}" | cut -d: -f4)
if [ -n "${ACTUAL_REGION}" ]; then
    REGION="${ACTUAL_REGION}"
    echo "📍 Detected Lambda region: ${REGION}"
fi

echo "✅ Lambda function created"
echo ""

# 5. Create EventBridge rule for scheduled execution (optional)
echo "⏰ Creating EventBridge rule for daily execution..."

RULE_NAME="${PROJECT_NAME}-daily-scrape"
aws events put-rule \
    --name "${RULE_NAME}" \
    --schedule-expression "cron(0 2 * * ? *)" \
    --description "Daily scrape at 2 AM UTC" \
    --state ENABLED \
    --region "${REGION}" || echo "⚠️  Rule may already exist"

# Add Lambda as target (remove existing target first if it exists, then add new one)
aws events remove-targets \
    --rule "${RULE_NAME}" \
    --ids "1" \
    --region "${REGION}" 2>/dev/null || true

aws events put-targets \
    --rule "${RULE_NAME}" \
    --targets "Id=1,Arn=${LAMBDA_ARN},Input='{\"username\":\"emily2636\",\"s3_bucket\":\"${BUCKET_NAME}\",\"s3_prefix\":\"emily2636\",\"incremental\":true,\"delay\":1.0}'" \
    --region "${REGION}" || echo "⚠️  Failed to add target (may already exist)"

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission \
    --function-name "${LAMBDA_FUNCTION_NAME}" \
    --statement-id "${PROJECT_NAME}-eventbridge-invoke" \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:${REGION}:$(aws sts get-caller-identity --query Account --output text):rule/${RULE_NAME}" \
    --region "${REGION}" || \
    echo "⚠️  Permission may already exist"

echo "✅ EventBridge rule created: ${RULE_NAME} (runs daily at 2 AM UTC)"
echo ""

# 6. Create CloudWatch Log Group (optional but recommended)
LOG_GROUP_NAME="/aws/lambda/${LAMBDA_FUNCTION_NAME}"
aws logs create-log-group \
    --log-group-name "${LOG_GROUP_NAME}" \
    --retention-in-days 7 \
    --region "${REGION}" || echo "⚠️  Log group may already exist"

echo "✅ CloudWatch log group created: ${LOG_GROUP_NAME}"
echo ""

# Cleanup temp files
rm -f /tmp/trust-policy.json /tmp/s3-policy.json /tmp/lifecycle.json /tmp/website-config.json /tmp/bucket-policy.json

echo "🎉 Infrastructure setup complete!"
echo ""
echo "Summary:"
echo "  S3 Bucket: s3://${BUCKET_NAME}"
echo "  Lambda Function: ${LAMBDA_FUNCTION_NAME}"
echo "  IAM Role: ${IAM_ROLE_NAME}"
echo "  EventBridge Rule: ${RULE_NAME} (daily at 2 AM UTC)"
echo ""
echo "To test the Lambda function:"
echo "  aws lambda invoke --function-name ${LAMBDA_FUNCTION_NAME} --payload '{\"username\":\"emily2636\",\"s3_bucket\":\"${BUCKET_NAME}\"}' response.json"
echo ""
echo "To update the Lambda function code:"
echo "  zip -r function.zip poshmark_scraper.py"
echo "  pip install -r requirements.txt -t package/"
echo "  cp poshmark_scraper.py package/"
echo "  cd package && zip -r ../function.zip . && cd .."
echo "  aws lambda update-function-code --function-name ${LAMBDA_FUNCTION_NAME} --zip-file fileb://function.zip"

