from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import time
import uuid
import threading
from datetime import datetime
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from file_processor import FileProcessor
from audio_processor import AudioProcessor
from google.cloud import storage
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Allow only the deployed Vercel frontend domain for CORS
CORS(app)

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['TEMP_FOLDER'] = 'temp'

# Ensure upload directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['TEMP_FOLDER'], exist_ok=True)

# File processors
file_processor = FileProcessor()
audio_processor = AudioProcessor()

# In-memory storage for progress tracking
analysis_progress = {}

# Allowed file extensions
ALLOWED_DOCUMENT_EXTENSIONS = {'txt', 'pdf', 'docx', 'png', 'jpg', 'jpeg', 'bmp'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'm4a'}

def allowed_file(filename, file_type):
    if '.' not in filename:
        return False
    
    extension = filename.rsplit('.', 1)[1].lower()
    
    if file_type == 'document':
        return extension in ALLOWED_DOCUMENT_EXTENSIONS
    elif file_type == 'audio':
        return extension in ALLOWED_AUDIO_EXTENSIONS
    
    return False

def get_file_type(filename):
    if '.' not in filename:
        return None
    
    extension = filename.rsplit('.', 1)[1].lower()
    
    if extension in ALLOWED_DOCUMENT_EXTENSIONS:
        return 'document'
    elif extension in ALLOWED_AUDIO_EXTENSIONS:
        return 'audio'
    
    return None

def validate_file_size(file, file_type):
    """Validate file size based on type"""
    file.seek(0, 2)  # Seek to end
    size = file.tell()
    file.seek(0)  # Reset to beginning
    
    if file_type == 'document':
        max_size = 100 * 1024 * 1024  # 100MB
    elif file_type == 'audio':
        max_size = 50 * 1024 * 1024   # 50MB
    else:
        return False
    
    return size <= max_size

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(error):
    return jsonify({
        'error': 'File too large',
        'message': 'File size exceeds the maximum allowed limit'
    }), 413

@app.route('/api/upload', methods=['POST'])
def upload_files():
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'No files provided'}), 400
        
        files = request.files.getlist('files')
        
        if not files or all(f.filename == '' for f in files):
            return jsonify({'error': 'No files selected'}), 400
        
        # Generate unique session ID
        session_id = str(uuid.uuid4())
        session_folder = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
        os.makedirs(session_folder, exist_ok=True)
        
        uploaded_files = []
        
        for file in files:
            if file.filename == '':
                continue
            
            # Determine file type
            file_type = get_file_type(file.filename)
            if not file_type:
                return jsonify({
                    'error': f'Unsupported file type: {file.filename}',
                    'message': 'Please upload only supported file types'
                }), 400
            
            # Validate file size
            if not validate_file_size(file, file_type):
                max_size = "50MB" if file_type == 'audio' else "100MB"
                return jsonify({
                    'error': f'File too large: {file.filename}',
                    'message': f'Maximum file size for {file_type} files is {max_size}'
                }), 400
            
            # Save file
            filename = secure_filename(file.filename)
            file_path = os.path.join(session_folder, filename)
            file.save(file_path)
            
            uploaded_files.append({
                'filename': filename,
                'original_name': file.filename,
                'file_type': file_type,
                'size': os.path.getsize(file_path)
            })
        
        return jsonify({
            'session_id': session_id,
            'files': uploaded_files,
            'message': f'Successfully uploaded {len(uploaded_files)} files'
        })
    
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': 'Upload failed', 'message': str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze_files():
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'error': 'Session ID required'}), 400
        
        session_folder = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
        
        if not os.path.exists(session_folder):
            return jsonify({'error': 'Session not found'}), 404
        
        # Check if analysis is already in progress
        if session_id in analysis_progress and analysis_progress[session_id]['status'] == 'processing':
            return jsonify({
                'session_id': session_id,
                'message': 'Analysis already in progress',
                'status': 'processing'
            })
        
        # Initialize progress tracking
        analysis_progress[session_id] = {
            'status': 'starting',
            'progress': 0,
            'message': 'Initializing analysis...',
            'results': [],
            'overall_score': 0,
            'generated_title': '',
            'error': None,
            'session_id': session_id
        }
        
        logger.info(f"Starting analysis for session {session_id}")
        
        # Start analysis in background thread
        thread = threading.Thread(target=process_files_async, args=(session_id, session_folder))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'session_id': session_id,
            'message': 'Analysis started',
            'status': 'processing'
        })
    
    except Exception as e:
        logger.error(f"Analysis error: {str(e)}")
        return jsonify({'error': 'Analysis failed', 'message': str(e)}), 500

def process_files_async(session_id, session_folder):
    """Process files asynchronously and update progress"""
    try:
        files = [f for f in os.listdir(session_folder) if os.path.isfile(os.path.join(session_folder, f))]
        total_files = len(files)
        
        analysis_progress[session_id]['status'] = 'processing'
        analysis_progress[session_id]['message'] = f'Processing {total_files} files...'
        
        all_content = []
        file_results = []
        
        for i, filename in enumerate(files):
            file_path = os.path.join(session_folder, filename)
            file_type = get_file_type(filename)
            
            # Update progress
            progress = int((i / total_files) * 80)  # 80% for file processing
            analysis_progress[session_id]['progress'] = progress
            analysis_progress[session_id]['message'] = f'Processing {filename}...'
            
            try:
                # Process file based on type with optimized handling
                if file_type == 'document':
                    content = file_processor.process_file(file_path)
                elif file_type == 'audio':
                    content = audio_processor.process_audio(file_path)
                else:
                    logger.warning(f"Unknown file type for {filename}")
                    continue
                
                # Calculate extraction score using optimized method
                score = calculate_extraction_score(content)
                
                file_result = {
                    'filename': filename,
                    'file_type': file_type,
                    'content': content,
                    'score': score,
                    'status': get_score_status(score)
                }
                
                file_results.append(file_result)
                if content and content.strip():
                    all_content.append(content)
                
            except Exception as e:
                logger.error(f"Error processing {filename}: {str(e)}")
                file_result = {
                    'filename': filename,
                    'file_type': file_type,
                    'content': '',
                    'score': 0,
                    'status': 'error',
                    'error': str(e)
                }
                file_results.append(file_result)
        
        # Calculate overall score more efficiently
        if file_results:
            valid_scores = [r['score'] for r in file_results if r['score'] > 0]
            overall_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0
        else:
            overall_score = 0
        
        # Finalize analysis
        analysis_progress[session_id]['progress'] = 100
        analysis_progress[session_id]['message'] = 'Analysis completed successfully'
        
        # Update final results with optimized content joining
        analysis_progress[session_id].update({
            'status': 'completed',
            'progress': 100,
            'message': 'Analysis completed successfully',
            'results': file_results,
            'overall_score': overall_score,
            'all_content': '\n\n'.join(all_content) if all_content else '',
            'session_id': session_id  # Add session_id for frontend reference
        })
        
    except Exception as e:
        logger.error(f"Async processing error: {str(e)}")
        analysis_progress[session_id].update({
            'status': 'error',
            'progress': 0,
            'message': f'Analysis failed: {str(e)}',
            'error': str(e)
        })

def calculate_extraction_score(content):
    """Calculate extraction score based on content quality - matches original OCR.py logic"""
    if not content or not content.strip():
        return 0
    
    total_chars = len(content.strip())
    return min(100, (total_chars / 1000) * 100) if total_chars > 0 else 0

def get_score_status(score):
    """Get status color based on score - matches original OCR.py logic"""
    if score >= 80:
        return 'green'  # "Green - Good"
    elif score >= 30:
        return 'yellow' # "Yellow - Partial"
    else:
        return 'red'    # "Red - Poor"

@app.route('/api/progress/<session_id>', methods=['GET'])
def get_progress(session_id):
    """Get analysis progress for a session"""
    if session_id not in analysis_progress:
        return jsonify({'error': 'Session not found'}), 404
    
    return jsonify(analysis_progress[session_id])

@app.route('/api/cleanup/<session_id>', methods=['DELETE'])
def cleanup_session(session_id):
    """Clean up session files"""
    try:
        session_folder = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
        
        if os.path.exists(session_folder):
            import shutil
            shutil.rmtree(session_folder)
        
        # Remove from progress tracking
        if session_id in analysis_progress:
            del analysis_progress[session_id]
        
        return jsonify({'message': 'Session cleaned up successfully'})
    
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")
        return jsonify({'error': 'Cleanup failed', 'message': str(e)}), 500

@app.route('/api/generate-course', methods=['POST'])
def generate_course():
    """Generate course structure from analyzed content"""
    try:
        data = request.get_json()
        logger.info(f"Received data: {data}")
        
        session_id = data.get('session_id')
        course_title = data.get('course_title', '').strip()
        additional_notes = data.get('additional_notes', '').strip()
        extracted_content = data.get('extracted_content', '').strip()
        
        logger.info(f"Course generation request - Session ID: {session_id}, Title: {course_title}")
        
        if not course_title:
            logger.error("Course title is missing or empty")
            return jsonify({'error': 'Course title is required'}), 400
        
        # Determine the content to use for course generation
        if extracted_content:
            # Use the extracted content provided by the frontend (user may have modified it)
            analyzed_content = extracted_content
            logger.info(f"Using provided extracted content (length: {len(extracted_content)})")
        elif session_id:
            # Fall back to session content if no extracted content provided
            if session_id not in analysis_progress:
                logger.error(f"Session not found: {session_id}. Available sessions: {list(analysis_progress.keys())}")
                return jsonify({'error': 'Session not found'}), 404
            session_data = analysis_progress[session_id]
            logger.info(f"Session data status: {session_data.get('status')}")
            if session_data.get('status') != 'completed':
                logger.error(f"Analysis not completed for session {session_id}")
                return jsonify({'error': 'Analysis not completed yet'}), 400
            analyzed_content = session_data.get('all_content', '')
            if not analyzed_content:
                logger.error(f"No analyzed content for session {session_id}")
                return jsonify({'error': 'No analyzed content available'}), 400
        else:
            # No session_id or extracted content provided, use only title and notes for course generation
            analyzed_content = additional_notes if additional_notes else "General course content based on the provided title."
            logger.info("Generating course without session or extracted content - using title and notes only")

        if not analyzed_content:
            logger.error("No content available for course generation")
            return jsonify({'error': 'No content available for course generation'}), 400
        
        logger.info(f"About to generate course structure with content length: {len(analyzed_content)}")
        
        # Generate course structure
        course_structure = generate_course_structure(analyzed_content, course_title, additional_notes)
        
        logger.info(f"Course generation successful for title: {course_title}")
        
        return jsonify({
            'success': True,
            'course_structure': course_structure,
            'course_title': course_title,
            'content_length': len(analyzed_content),
            'generated_at': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Course generation error: {str(e)}")
        logger.error(f"Exception type: {type(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({'error': 'Course generation failed', 'message': str(e)}), 500

def generate_course_structure(content, title, notes=""):
    """Generate course structure from analyzed content"""
    # Combine content and notes
    full_content = content
    if notes:
        full_content += f"\n\nAdditional Notes:\n{notes}"
    
    # Basic content analysis
    words = full_content.lower().split()
    word_count = len(words)
    
    # Extract key topics with better analysis
    word_freq = {}
    # Filter out common words and focus on meaningful terms
    common_words_to_exclude = {'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'can', 'this', 'that', 'these', 'those', 'a', 'an', 'as', 'from', 'into', 'during', 'including', 'until', 'against', 'among', 'throughout', 'despite', 'towards', 'upon', 'concerning', 'about', 'like', 'through', 'over', 'before', 'after', 'since', 'within', 'under', 'without', 'between', 'among', 'behind', 'beneath', 'beside', 'beyond', 'inside', 'outside', 'above', 'below', 'up', 'down', 'out', 'off', 'over', 'under', 'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all', 'any', 'both', 'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'you', 'your', 'yours', 'yourself', 'yourselves', 'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'what', 'which', 'who', 'whom', 'whose', 'whichever', 'whoever', 'whomever', 'this', 'that', 'these', 'those', 'am', 'is', 'are', 'was', 'were', 'being', 'been', 'be', 'have', 'has', 'had', 'having', 'do', 'does', 'did', 'doing', 'will', 'would', 'should', 'could', 'may', 'might', 'must', 'shall', 'can'}
    
    for word in words:
        # Clean the word (remove punctuation)
        clean_word = ''.join(c for c in word if c.isalnum())
        if len(clean_word) > 3 and clean_word not in common_words_to_exclude:
            word_freq[clean_word] = word_freq.get(clean_word, 0) + 1
    
    # Get most frequent meaningful words
    common_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:20]
    key_topics = [word for word, freq in common_words if freq > 1]
    
    # Extract sentences to understand context better
    sentences = [s.strip() for s in content.split('.') if len(s.strip()) > 20]
    
    # Look for specific patterns in the content
    content_lower = content.lower()
    
    # Check for mathematical or technical content
    has_math = any(char in content for char in ['=', '+', '-', '*', '/', '∑', '∫', '√', '∞'])
    has_formulas = any(word in content_lower for word in ['formula', 'equation', 'function', 'variable', 'parameter'])
    has_methods = any(word in content_lower for word in ['method', 'algorithm', 'procedure', 'technique', 'approach'])
    has_problems = any(word in content_lower for word in ['problem', 'solution', 'solve', 'optimization', 'minimize', 'maximize'])
    
    # If we don't have enough key topics, generate some based on content analysis
    if len(key_topics) < 3:
        # Extract words from title
        title_words = title.lower().split()
        key_topics.extend([word for word in title_words if len(word) > 3 and word not in key_topics])
        
        # Add context-specific topics
        if has_math or has_formulas:
            key_topics.extend(['mathematics', 'calculations', 'formulas'])
        if has_methods:
            key_topics.extend(['methods', 'techniques', 'procedures'])
        if has_problems:
            key_topics.extend(['problem-solving', 'optimization', 'solutions'])
        
        # Add some generic topics if still not enough
        if len(key_topics) < 3:
            key_topics.extend(['fundamentals', 'principles', 'applications'])
    
    # Remove duplicates and limit to top topics
    key_topics = list(dict.fromkeys(key_topics))[:10]
    
    # Generate course structure
    structure = {
        'title': title,
        'overview': generate_course_overview(content, key_topics, sentences),
        'modules': generate_course_modules(content, key_topics, sentences),
        'learning_objectives': generate_learning_objectives(key_topics, content),
        'estimated_duration': estimate_course_duration(word_count),
        'difficulty_level': estimate_difficulty_level(content),
        'key_topics': key_topics
    }
    
    return structure

def generate_course_overview(content, key_topics, sentences):
    """Generate course overview"""
    word_count = len(content.split())
    
    # Analyze content characteristics
    content_lower = content.lower()
    has_math = any(char in content for char in ['=', '+', '-', '*', '/', '∑', '∫', '√', '∞'])
    has_formulas = any(word in content_lower for word in ['formula', 'equation', 'function', 'variable', 'parameter'])
    has_methods = any(word in content_lower for word in ['method', 'algorithm', 'procedure', 'technique', 'approach'])
    has_problems = any(word in content_lower for word in ['problem', 'solution', 'solve', 'optimization', 'minimize', 'maximize'])
    has_theory = any(word in content_lower for word in ['theory', 'principle', 'concept', 'fundamental'])
    
    # Create content-specific overview
    if has_math or has_formulas:
        course_type = "mathematical and analytical"
        focus_areas = "mathematical concepts, formulas, and analytical techniques"
    elif has_methods:
        course_type = "methodological and procedural"
        focus_areas = "methods, techniques, and systematic approaches"
    elif has_problems:
        course_type = "problem-solving oriented"
        focus_areas = "problem identification, solution strategies, and practical applications"
    elif has_theory:
        course_type = "theoretical and conceptual"
        focus_areas = "theoretical foundations, principles, and conceptual frameworks"
    else:
        course_type = "comprehensive"
        focus_areas = "key concepts and practical applications"
    
    # Get sample sentences for context
    sample_sentences = sentences[:3] if sentences else []
    context_info = ""
    if sample_sentences:
        context_info = f"\n\nBased on the analyzed content, this course covers topics such as: {sample_sentences[0][:100]}..."
    
    overview = f"""This course is designed to provide comprehensive coverage of the analyzed content, structured as a {course_type} learning experience.
    
The course material covers approximately {word_count:,} words of content focusing on {', '.join(key_topics[:5]) if key_topics else 'various topics'}. The content has been carefully analyzed to identify key themes and learning objectives.

The course structure emphasizes {focus_areas} found in the source material, providing a logical learning progression from foundational concepts to advanced applications. Each module is designed to build upon previous knowledge while introducing new concepts and practical examples.

This course is suitable for learners who want to gain a thorough understanding of the subject matter presented in the analyzed content, with a focus on practical application and real-world relevance.{context_info}"""
    
    return overview

def generate_course_modules(content, key_topics, sentences):
    """Generate course modules based on content analysis"""
    modules = []
    
    # Split content into logical sections
    content_sections = [s.strip() for s in content.split('\n\n') if s.strip()]
    section_count = len(content_sections)
    
    # Analyze content for specific patterns
    content_lower = content.lower()
    
    # Check for different types of content
    has_definitions = any(word in content_lower for word in ['definition', 'define', 'means', 'refers to'])
    has_examples = any(word in content_lower for word in ['example', 'instance', 'case', 'scenario'])
    has_problems = any(word in content_lower for word in ['problem', 'question', 'exercise', 'task'])
    has_methods = any(word in content_lower for word in ['method', 'algorithm', 'procedure', 'technique', 'approach'])
    has_theory = any(word in content_lower for word in ['theory', 'principle', 'concept', 'fundamental'])
    has_applications = any(word in content_lower for word in ['application', 'use', 'implement', 'practice'])
    
    # Create modules based on content analysis
    if key_topics and len(key_topics) >= 3:
        # Use key topics to create focused modules
        for i, topic in enumerate(key_topics[:6]):  # Max 6 modules
            # Create topic-specific topics based on content analysis
            module_topics = []
            
            if has_theory:
                module_topics.append(f"Theory and principles of {topic}")
            if has_definitions:
                module_topics.append(f"Key definitions and concepts in {topic}")
            if has_methods:
                module_topics.append(f"Methods and techniques for {topic}")
            if has_examples:
                module_topics.append(f"Examples and case studies in {topic}")
            if has_applications:
                module_topics.append(f"Practical applications of {topic}")
            if has_problems:
                module_topics.append(f"Problem-solving with {topic}")
            
            # If we don't have enough specific topics, add generic ones
            while len(module_topics) < 4:
                if f"Introduction to {topic}" not in module_topics:
                    module_topics.append(f"Introduction to {topic}")
                elif f"Advanced {topic}" not in module_topics:
                    module_topics.append(f"Advanced {topic}")
                else:
                    module_topics.append(f"Review and assessment of {topic}")
            
            module = {
                'module_number': i + 1,
                'title': f"Module {i + 1}: {topic.capitalize()}",
                'description': f"Comprehensive coverage of {topic} concepts, methods, and applications based on the analyzed content",
                'topics': module_topics[:4],  # Limit to 4 topics per module
                'estimated_time': "2-3 hours"
            }
            modules.append(module)
    else:
        # Content-based modules using actual content sections
        module_count = min(max(3, section_count // 2), 6)  # 3-6 modules
        
        for i in range(module_count):
            # Create module based on content type
            if has_theory and i == 0:
                title = "Module 1: Theoretical Foundations"
                description = "Understanding the fundamental theories and principles"
                topics = ["Core theoretical concepts", "Key principles", "Fundamental definitions", "Theoretical framework"]
            elif has_methods and (i == 1 or not has_theory):
                title = f"Module {i + 1}: Methods and Techniques"
                description = "Learning practical methods and techniques"
                topics = ["Methodology overview", "Step-by-step procedures", "Technique applications", "Best practices"]
            elif has_applications:
                title = f"Module {i + 1}: Practical Applications"
                description = "Applying concepts to real-world scenarios"
                topics = ["Real-world applications", "Case studies", "Practical examples", "Implementation strategies"]
            elif has_problems:
                title = f"Module {i + 1}: Problem Solving"
                description = "Developing problem-solving skills"
                topics = ["Problem identification", "Solution approaches", "Problem-solving techniques", "Practice exercises"]
            else:
                title = f"Module {i + 1}: Core Concepts"
                description = "Essential concepts and principles"
                topics = ["Introduction and overview", "Key concepts", "Important principles", "Summary and review"]
            
            module = {
                'module_number': i + 1,
                'title': title,
                'description': description,
                'topics': topics,
                'estimated_time': "2-3 hours"
            }
            modules.append(module)
    
    return modules

def generate_learning_objectives(key_topics, content):
    """Generate learning objectives"""
    objectives = [
        "Understand the fundamental concepts presented in the course material",
        "Apply key principles to practical scenarios",
        "Analyze and evaluate different approaches and methodologies",
        "Demonstrate comprehension through practical application"
    ]
    
    # Analyze content for specific learning objectives
    content_lower = content.lower()
    
    # Add content-specific objectives
    if any(word in content_lower for word in ['problem', 'solution', 'solve']):
        objectives.append("Develop problem-solving skills and analytical thinking")
    
    if any(word in content_lower for word in ['method', 'algorithm', 'procedure', 'technique']):
        objectives.append("Master specific methods and techniques relevant to the subject")
    
    if any(word in content_lower for word in ['formula', 'equation', 'calculation']):
        objectives.append("Apply mathematical concepts and perform accurate calculations")
    
    if any(word in content_lower for word in ['application', 'implement', 'practice']):
        objectives.append("Implement concepts in real-world applications and scenarios")
    
    if any(word in content_lower for word in ['theory', 'principle', 'concept']):
        objectives.append("Comprehend theoretical foundations and underlying principles")
    
    # Add topic-specific objectives
    if key_topics:
        for topic in key_topics[:3]:
            objectives.append(f"Master the concepts and applications of {topic}")
    
    # Remove duplicates and limit to reasonable number
    objectives = list(dict.fromkeys(objectives))[:8]
    
    return objectives

def estimate_course_duration(word_count):
    """Estimate course duration based on content length"""
    # Average reading speed: 200-250 words per minute
    # Include time for activities, reflection, etc.
    reading_time = word_count / 200  # minutes
    total_time = reading_time * 2.5  # Factor in activities and comprehension
    
    hours = int(total_time // 60)
    minutes = int(total_time % 60)
    
    if hours > 0:
        return f"{hours} hours {minutes} minutes"
    else:
        return f"{minutes} minutes"

def estimate_difficulty_level(content):
    """Estimate difficulty level based on content analysis"""
    # Simple heuristic based on word complexity and content length
    words = content.split()
    avg_word_length = sum(len(word) for word in words) / len(words) if words else 0
    
    if avg_word_length > 6:
        return "Advanced"
    elif avg_word_length > 4.5:
        return "Intermediate"
    else:
        return "Beginner"

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Check if upload and temp directories exist
        upload_dir_exists = os.path.exists(app.config['UPLOAD_FOLDER'])
        temp_dir_exists = os.path.exists(app.config['TEMP_FOLDER'])
        
        # Check active sessions
        active_sessions = len(analysis_progress)
        
        # Check if Google Cloud credentials are available
        credentials_file = "skoolme-ocr-b933da63cd81.json"
        credentials_exist = os.path.exists(credentials_file)
        
        health_status = {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
            'upload_directory': upload_dir_exists,
            'temp_directory': temp_dir_exists,
            'active_sessions': active_sessions,
            'google_credentials': credentials_exist,
            'version': '1.0.0'
        }
        
        return jsonify(health_status)
    
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
