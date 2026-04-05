document.addEventListener('DOMContentLoaded', () => {

    // Auth Form Logic
    const roleSelect = document.getElementById('role');
    const skillsGroup = document.getElementById('skills-group');
    
    if (roleSelect && skillsGroup) {
        roleSelect.addEventListener('change', (e) => {
            if (e.target.value === 'worker') {
                skillsGroup.style.display = 'block';
                document.getElementById('skills').required = true;
            } else {
                skillsGroup.style.display = 'none';
                document.getElementById('skills').required = false;
            }
        });
        
        // Trigger initial state
        roleSelect.dispatchEvent(new Event('change'));
    }

    // Star Rating Logic
    const starForms = document.querySelectorAll('.review-form');
    starForms.forEach(form => {
        const inputs = form.querySelectorAll('.star-rating input');
        inputs.forEach(input => {
            input.addEventListener('change', async (e) => {
                const rating = e.target.value;
                const workerId = form.dataset.workerId;
                
                try {
                    const response = await fetch('/api/reviews', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ worker_id: workerId, rating: rating })
                    });
                    
                    const result = await response.json();
                    if (result.success) {
                        alert(`Successfully rated worker ${workerId} with ${rating} stars!`);
                    } else {
                        alert('Error submitting rating. Please try again.');
                    }
                } catch (error) {
                    console.error('Error submitting review:', error);
                }
            });
        });
    });

    // Online/Offline Status Toggle Logic
    const statusToggle = document.getElementById('status-toggle');
    if (statusToggle) {
        statusToggle.addEventListener('change', async (e) => {
            const isOnline = e.target.checked;
            
            try {
                const response = await fetch('/api/worker/toggle_status', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ is_online: isOnline })
                });
                
                const result = await response.json();
                if (result.success) {
                    const statusText = document.getElementById('status-text');
                    statusText.textContent = result.is_online ? 'Online' : 'Offline';
                } else {
                    alert('Error updating status.');
                    e.target.checked = !isOnline; // Restore
                }
            } catch (error) {
                console.error('Error updating status:', error);
                e.target.checked = !isOnline; // Restore
            }
        });
    }

});
